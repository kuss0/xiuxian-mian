import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from model import app, message_log_recovery, persistence, runtime, state as state_module
from model.features import concubine, concubine_affinity_actions as affinity_actions, passive_inbox
from tests import test_concubine_fragment_actions as fragment_tests
from tests.test_concubine_fragment_actions import ACCOUNT, BOT, CHAT, ID, NAME, NOW, ROOT


FIELD = "concubine_status_query"
base_env = fragment_tests.env
COMMAND = concubine.CMD_CONCUBINE_VOYAGE_STATUS
LOCK = "\u4f8d\u59be\u4ecd\u5728\u8fdc\u822a\u9014\u4e2d\uff0c\u6682\u65e0\u6cd5\u4e0e\u4f60\u540c\u68a6\u5bfb\u56fe\u3002"
IDLE = f"\u4f8d\u59be\u3010{NAME}\u3011\u5f53\u524d\u5e76\u672a\u6267\u884c\u8fdc\u822a\u4efb\u52a1\u3002"
NO_SETTLEMENT = "\u4f8d\u59be\u5f53\u524d\u5e76\u65e0\u53ef\u7ed3\u7b97\u7684\u8fdc\u822a\u4efb\u52a1"
SAILING = "\u8fdc\u822a\u72b6\u6001: \u6708\u6bbf\u5bfb\u75d5\u822a\u7ebf\u8fdb\u884c\u4e2d\uff0c\u5269\u4f59\u7ea6 319 \u5206\u949f\u3002"
RETURNED = "\u8fdc\u822a\u72b6\u6001: \u6708\u6bbf\u5bfb\u75d5\u822a\u7ebf\u5df2\u5f52\u822a\uff0c\u5f85\u7ed3\u7b97\uff08.\u8fdc\u822a\u5f52\u6765\uff09\u3002"


@pytest.fixture
def env(base_env, monkeypatch):
    base_env.identity.update(concubine_voyage_enabled=True)
    monkeypatch.setattr(affinity_actions, "_INFLIGHT", {})
    return base_env


async def start(env):
    with state_module.use_identity(ID):
        return await concubine._send_voyage_status_command(env.clock[0])


def receipt(env, *, root=ROOT, chat=CHAT):
    record = env.identity[FIELD]
    env.identity["pending_tasks"][(chat, root)] = {
        "cmd": COMMAND, "family": runtime.resolve_reply_family(COMMAND),
        "op_id": record["op_id"], "source_module": concubine.CONCUBINE_QUERY_SOURCE,
        "account_id": ACCOUNT, "chat_id": chat, "message_id": root,
        "sent_at": NOW + .5, "send_started_at": NOW,
    }


async def reply(env, text=IDLE, *, route="direct", root=ROOT, chat=CHAT, at=NOW + 1, context=None):
    parent = SimpleNamespace(id=root, chat_id=chat, sender_id=ID, raw_text=COMMAND)
    event = SimpleNamespace(id=root + 1, chat_id=chat, sender_id=BOT, server_event_at=at)
    if context is None:
        context = {"family": "concubine_voyage", "send_as_id": ID, "account_id": ACCOUNT,
                   "chat_id": chat, "root_msg_id": root, "reply_to_msg_id": root,
                   "reply_to_command": COMMAND, "sender_id": BOT}
    now = max(env.clock[0], at) + 1
    if route == "native":
        return await app._handle_routed_reply_event(event, text, now, parent, context)
    if route == "passive":
        return await passive_inbox.handle_passive_module_card(
            text, now=now, reply_context=context, event=event, event_type="message")
    with state_module.use_identity(ID):
        return await concubine.handle_concubine_voyage_reply(
            text, now, parent, matched_family="concubine_voyage", current_msg_id=root + 1,
            current_chat_id=chat, observed_at=at, reply_context=context)


@pytest.mark.parametrize("retry_count", [0, 2])
def test_unknown_voyage_uses_owned_status_read_never_settlement_probe(env, retry_count):
    env.identity.update(concubine_voyage_status="sailing", concubine_voyage_return_at=0,
                        concubine_voyage_retry_count=retry_count, next_concubine_time=NOW)
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(NOW))
    assert env.send.await_args.args == (COMMAND,)
    assert env.send.await_args.kwargs["track"] is True
    assert env.send.await_args.kwargs["max_retry"] == 0
    assert env.identity[FIELD]["kind"] == "voyage_status"
    assert env.identity[FIELD]["status"] == "sent"


def test_voyage_query_intent_is_saved_before_dispatch(env):
    async def sent(command, **kwargs):
        record = env.identity[FIELD]
        assert record["kind"] == "voyage_status" and record["status"] == "sending"
        assert record["partner"] == NAME
        assert (record["identity_id"], record["account_id"], record["chat_id"]) == (ID, ACCOUNT, CHAT)
        assert record["command"] == command == COMMAND
        assert kwargs["track"] is True and kwargs["max_retry"] == 0
        assert kwargs["op_id"] == record["op_id"] and kwargs["operation_check"]()
        env.save.assert_called()
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(start(env))


@pytest.mark.parametrize("kind", ["greet", "fragment"])
@pytest.mark.parametrize("extra", ["", "\n\u5171\u5386\u5fc3\u52ab\u51b7\u5374: 2\u5c0f\u65f6\u3002"])
def test_untimed_voyage_rejection_is_independent_of_unrelated_cache_and_duration(env, kind, extra):
    with state_module.use_identity(ID):
        record = {"kind": "fragment", "partner": NAME, "affinity": 270}

        def parse():
            return (affinity_actions._parse_result("greet", LOCK + extra, record, NOW)
                    if kind == "greet" else concubine._parse_query_reply(record, LOCK + extra, NOW))

        empty = parse()
        env.identity["concubine_voyage_return_at"] = NOW + 3600
        cached = parse()
    assert empty is not None and cached == empty
    assert empty["outcome"] == "voyage_lock"
    assert (empty["wait_until"] if kind == "greet" else empty["voyage"]["return_at"]) == 0


@pytest.mark.parametrize("return_at", [0, NOW - 5])
def test_voyage_projection_never_substitutes_a_cached_future_time(env, return_at):
    env.identity["concubine_voyage_return_at"] = NOW + 86400
    with state_module.use_identity(ID):
        assert concubine._apply_voyage_snapshot({"status": "sailing", "return_at": return_at}, NOW)
    assert env.identity["concubine_voyage_return_at"] == return_at


def test_real_returned_panel_with_settlement_hint_is_valid(env):
    with state_module.use_identity(ID):
        parsed = concubine._parse_status_voyage(RETURNED.split(": ", 1)[1], NOW)
    assert parsed is not None and parsed["status"] == "returned"


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_owned_voyage_status_closes_exact_pending_and_is_idempotent(env, route):
    env.identity.update(concubine_voyage_status="sailing", concubine_voyage_return_at=0)
    assert asyncio.run(start(env))
    receipt(env)
    env.identity["pending_tasks"][(CHAT - 1, ROOT)] = dict(
        env.identity["pending_tasks"][(CHAT, ROOT)], chat_id=CHAT - 1)
    assert asyncio.run(reply(env, route=route))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_voyage_status"] == "idle"
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert (CHAT - 1, ROOT) in env.identity["pending_tasks"]
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, route=route))
    assert env.identity == before


def test_voyage_read_uses_actual_runtime_family_without_becoming_a_mutation(env):
    assert runtime.resolve_reply_family(COMMAND) == "concubine_voyage"
    assert asyncio.run(start(env))
    receipt(env)
    with state_module.use_identity(ID):
        assert not concubine._status_snapshot_block_reason(NOW + 1, allow_status_pending=True)
        env.identity["pending_tasks"][(CHAT, ROOT)]["cmd"] = concubine.CMD_CONCUBINE_VOYAGE_RETURN
        assert concubine._status_snapshot_block_reason(NOW + 1, allow_status_pending=True)


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_voyage_status_without_owned_request_cannot_clear_sailing(env, route):
    env.identity.update(concubine_voyage_status="sailing", concubine_voyage_return_at=NOW + 3600)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, route=route))
    assert env.identity == before


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_nothing_to_settle_is_not_an_authoritative_idle_status(env, route):
    env.identity.update(concubine_voyage_status="returned", concubine_voyage_return_at=NOW - 1)
    assert asyncio.run(start(env))
    receipt(env)
    assert asyncio.run(reply(env, NO_SETTLEMENT, route=route))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_voyage_status"] == "needs_status"
    assert env.identity["concubine_voyage_return_at"] == 0
    assert env.identity["next_concubine_time"] > NOW + 1


def test_early_read_completion_cannot_be_rewound_by_send_return(env):
    async def sent(*_args, **_kwargs):
        receipt(env)
        assert await reply(env)
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_phase"] == "idle"
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]


@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_late_voyage_read_receipt_cannot_write_a_different_owner(env, change):
    after = {}

    async def sent(*_args, **_kwargs):
        if change == "delete":
            state_module.remove_identity(ID)
        elif change == "replace":
            state_module._meta_state["identity_states"][ID] = copy.deepcopy(env.identity)
        else:
            state_module.set_identity_account(ID, ACCOUNT + 1)
        after.update(copy.deepcopy(state_module._meta_state))
        return env.send.return_value

    env.send.side_effect = sent
    assert not asyncio.run(start(env))
    assert state_module._meta_state == after


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_failed_voyage_read_completion_remains_replayable(env, mode):
    env.identity.update(concubine_voyage_status="sailing", concubine_voyage_return_at=0)
    assert asyncio.run(start(env))
    receipt(env)
    before = copy.deepcopy(env.identity)
    if mode == "exception":
        env.save.side_effect = OSError("fixture")
        with pytest.raises(OSError):
            asyncio.run(reply(env))
        env.save.side_effect = None
    else:
        env.save.return_value = False
        assert not asyncio.run(reply(env))
    assert env.identity == before
    env.save.return_value = True
    assert asyncio.run(reply(env))
    assert env.identity["concubine_voyage_status"] == "idle"


def test_unanchored_passive_voyage_projection_has_no_authority(env):
    env.identity.update(concubine_voyage_status="sailing", concubine_voyage_return_at=NOW + 3600)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not asyncio.run(passive_inbox.handle_passive_module_card(
            IDLE, now=NOW + 1, reply_context={"send_as_id": ID, "family": "concubine_voyage"},
            event=SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT, server_event_at=NOW),
            event_type="message",
        ))
    assert env.identity == before


def tick(env, at):
    env.clock[0] = at
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(at))


def set_receipt(env, root, at):
    env.send.return_value = SimpleNamespace(id=root, chat_id=CHAT, sent_at=at, send_started_at=at)


@pytest.mark.parametrize("kind", ["greet", "dream", "puzzle", "fragment"])
@pytest.mark.parametrize("terminal", [IDLE, RETURNED])
def test_untimed_refusal_then_owned_status_restores_liveness_without_consuming_action(env, kind, terminal):
    env.identity["concubine_voyage_return_at"] = NOW + 86400
    if kind in {"dream", "puzzle"}:
        fragment_tests.prepare(env, kind)
        assert asyncio.run(fragment_tests.start(env, kind))
    else:
        if kind == "greet":
            state_module.update_send_as_profile(ID, sect_name="\u661f\u5bab")
            env.identity.update(concubine_tianji_enabled=True, concubine_affinity=270, concubine_last_greet_day="")
        else:
            with state_module.use_identity(ID):
                concubine._set_fragment_progress("xutian", 4, 4)
        with state_module.use_identity(ID):
            assert asyncio.run(getattr(concubine, f"_send_{kind}_command")(NOW))
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert asyncio.run(getattr(concubine, f"handle_concubine_{kind}_reply")(
            LOCK, NOW + 2, SimpleNamespace(id=ROOT, chat_id=CHAT, raw_text=""), current_msg_id=ROOT + 1,
            matched_family="concubine_" + kind,
            current_chat_id=CHAT, observed_at=NOW + 1, reply_context={"sender_id": BOT}))
    for key in ("concubine_dream_due_at", "concubine_affinity", "concubine_last_greet_day",
                "concubine_fragment_xutian_count", "concubine_fragment_cangkun_count"):
        assert env.identity[key] == before[key]
    assert env.identity["concubine_voyage_return_at"] == 0
    assert env.identity["concubine_voyage_status"] == "sailing"
    due = env.identity["next_concubine_time"]
    assert due == NOW + 2 + concubine.CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC
    tick(env, due - 1)
    env.send.assert_awaited_once()
    set_receipt(env, ROOT + 10, due)
    tick(env, due)
    assert env.send.await_args.args == (COMMAND,)
    assert asyncio.run(reply(env, terminal, root=ROOT + 10, at=due + 1))
    assert env.identity["concubine_voyage_status"] == ("idle" if terminal == IDLE else "returned")
    after = env.identity["next_concubine_time"]
    set_receipt(env, ROOT + 20, after)
    tick(env, after)
    expected = {"greet": concubine.CMD_CONCUBINE_DAILY_GREET, "dream": concubine.CMD_CONCUBINE_DREAM,
                "puzzle": concubine.CMD_CONCUBINE_FRAGMENT, "fragment": concubine.CMD_CONCUBINE_FRAGMENT}
    assert env.send.await_args.args == (expected[kind] if terminal == IDLE else concubine.CMD_CONCUBINE_VOYAGE_RETURN,)
    assert env.send.await_count == 3


@pytest.mark.parametrize("mode", ["none", "cancel", "exception"])
def test_unknown_read_survives_restore_then_only_read_can_retry_after_expiry(env, mode):
    env.identity.update(concubine_voyage_status="sailing", concubine_voyage_return_at=0)
    env.send.return_value = None
    if mode == "none":
        assert not asyncio.run(start(env))
    else:
        error = asyncio.CancelledError if mode == "cancel" else RuntimeError
        env.send.side_effect = error()
        with pytest.raises(error):
            asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unknown"
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 1)
    assert env.identity == before
    tick(env, NOW + 100)
    assert env.identity[FIELD]["status"] == "unknown"
    tick(env, NOW + concubine.CONCUBINE_PHASE_TIMEOUT_SEC + 1)
    assert env.identity[FIELD]["status"] == "expired"
    assert env.identity["concubine_voyage_status"] == "sailing"
    assert env.identity["concubine_voyage_return_at"] == 0
    env.send.assert_awaited_once()
    due = env.identity["next_concubine_time"]
    assert due > env.clock[0]
    env.send.side_effect = None
    set_receipt(env, ROOT + 10, due)
    tick(env, due)
    assert env.send.await_args.args == (COMMAND,)
    assert env.identity[FIELD]["status"] == "sent"


@pytest.mark.parametrize("change", ["global", "identity", "modules", "partner", "chat", "schedule", "phase", "summary"])
def test_queued_voyage_read_rechecks_controls_and_plan(env, change, monkeypatch):
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(_command, **kwargs):
        assert kwargs["operation_check"]()
        if change == "global":
            state_module.set_global_enabled(False)
        elif change == "identity":
            state_module.set_identity_enabled(ID, False)
        elif change == "modules":
            env.identity.update(concubine_enabled=False, concubine_voyage_enabled=False)
        elif change == "partner":
            env.identity["concubine_name"] = "replacement"
        elif change == "chat":
            state_module.set_game_group_id(CHAT - 1)
        elif change == "schedule":
            env.identity["next_concubine_time"] = NOW + 99999
        elif change == "phase":
            env.identity["concubine_phase"] = "heart_pending"
        else:
            env.identity.update(deep_retreat_enabled=True, deep_retreat_phase="observing_summary")
        assert not kwargs["operation_check"]()
        block.update(status="unsent", code="operation_cancelled", at=NOW)
        return None

    env.send.side_effect = sent
    assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unsent"
    if change == "schedule":
        assert env.identity["next_concubine_time"] == NOW + 99999


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
@pytest.mark.parametrize("field,value", [
    ("account_id", ACCOUNT + 1), ("send_as_id", ID + 1), ("chat_id", CHAT - 1),
    ("root_msg_id", ROOT - 1), ("reply_to_msg_id", ROOT - 1),
    ("source_module", "foreign"), ("op_id", "foreign"),
    ("reply_to_command_edited", True), ("reply_to_command", concubine.CMD_CONCUBINE_VOYAGE_RETURN),
])
def test_wrong_voyage_query_ownership_cannot_complete_or_modify_status(env, route, field, value):
    assert asyncio.run(start(env))
    receipt(env)
    before = copy.deepcopy(env.identity)
    context = {"family": "concubine_voyage", "send_as_id": ID, "account_id": ACCOUNT,
               "chat_id": CHAT, "root_msg_id": ROOT, "reply_to_msg_id": ROOT, "sender_id": BOT}
    context[field] = value
    assert not asyncio.run(reply(env, route=route, context=context))
    assert env.identity == before


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
@pytest.mark.parametrize("text", ["working", IDLE.replace(NAME, "another"), SAILING + "\n" + IDLE,
                                 RETURNED + "\n" + LOCK, LOCK + "\n" + IDLE])
def test_incomplete_foreign_or_conflicting_status_keeps_owned_read(env, route, text):
    assert asyncio.run(start(env))
    receipt(env)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, text, route=route))
    assert env.identity == before


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_voyage_status_uses_original_chat_and_server_clock(env, route):
    assert asyncio.run(start(env))
    receipt(env)
    state_module.set_game_group_id(CHAT - 1)
    env.clock[0] = NOW + 120
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, SAILING, route=route, chat=CHAT - 1))
    assert env.identity == before
    assert asyncio.run(reply(env, SAILING, route=route))
    assert env.identity[FIELD]["status"] == "complete"
    # A changed route is a changed business plan, not authority to rewrite it.
    assert env.identity["concubine_voyage_return_at"] == before["concubine_voyage_return_at"]


@pytest.mark.parametrize("text", [SAILING, RETURNED, IDLE, NO_SETTLEMENT, LOCK])
def test_status_parser_never_uses_cached_partner_route_or_return(env, text):
    with state_module.use_identity(ID):
        before = concubine._parse_voyage_status_text(text, NOW)
        env.identity.update(concubine_name="another", concubine_voyage_route="cached", concubine_voyage_return_at=NOW + 99999)
        after = concubine._parse_voyage_status_text(text, NOW)
    assert before is not None and after == before


@pytest.mark.parametrize("text,wait", [
    (SAILING, 319 * 60),
    (f"\u4f8d\u59be\u3010{NAME}\u3011\u6b63\u5728\u6267\u884c\u3010\u5192\u9669\u3011\u8fdc\u822a\uff0c\u9884\u8ba1\u5f52\u822a\u8fd8\u9700 3\u5c0f\u65f6\u3002", 10800),
    ("\u4f8d\u59be\u4ecd\u5728\u8fdc\u822a\u4e2d\uff0c\u8bf7\u5728 11\u5c0f\u65f648\u5206\u949f5\u79d2 \u540e\u518d\u8bd5\u3002", 42485),
    ("\u4f8d\u59be\u6b63\u5728\u8fdc\u822a\u9014\u4e2d\uff0c\u8fd8\u9700 30\u5206\u949f\u540e\u5f52\u6765\u3002", 1800),
])
def test_exact_voyage_waits_use_evidence_time_not_processing_time(env, text, wait):
    assert asyncio.run(start(env))
    env.clock[0] = NOW + 120
    assert asyncio.run(reply(env, text, at=NOW + 1))
    assert env.identity["concubine_voyage_return_at"] == NOW + 1 + wait + concubine.CD_BUFFER_SEC


@pytest.mark.parametrize("text", [IDLE, RETURNED, SAILING])
def test_old_status_cannot_unlock_or_refresh_voyage(env, text):
    env.identity.update(concubine_voyage_status="sailing", concubine_voyage_return_at=0)
    assert asyncio.run(start(env))
    env.clock[0] = NOW + concubine.CONCUBINE_PANEL_REUSE_MAX_AGE_SEC + 100
    assert asyncio.run(reply(env, text))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_voyage_status"] == "needs_status"
    assert env.identity["concubine_voyage_return_at"] == 0


def test_voyage_status_ownership_blocks_untracked_mutation_helpers(env):
    assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine._send_voyage_command(NOW))
        assert not asyncio.run(concubine._send_voyage_return_command(NOW))
    env.send.assert_awaited_once()
    assert env.identity == before


def logged_reply(text=IDLE, **changes):
    return dict({"event_type": "message", "sender_is_bot": True, "sender_id": BOT,
                 "chat_id": CHAT, "reply_to_msg_id": ROOT, "message_id": ROOT + 1,
                 "server_event_at": NOW + 1, "text": text}, **changes)


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_voyage_recovery_checkpoint_failure_rolls_back_before_scanning(env, monkeypatch, mode):
    assert asyncio.run(start(env))
    receipt(env)
    before = copy.deepcopy(env.identity)
    scan = Mock(return_value=[logged_reply()])
    monkeypatch.setattr(concubine, "find_message_log_replies", scan)
    env.save.reset_mock()
    env.save.return_value = False
    if mode == "exception":
        env.save.side_effect = OSError("fixture")
        with pytest.raises(OSError):
            tick(env, NOW + 1001)
    else:
        tick(env, NOW + 1001)
    assert env.identity == before
    scan.assert_not_called()
    env.save.assert_called_once()


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_valid_voyage_evidence_cannot_be_expired_after_failed_completion_save(env, monkeypatch, mode):
    assert asyncio.run(start(env))
    receipt(env)
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[logged_reply()]))
    env.save.reset_mock()
    env.save.side_effect = [True, False if mode == "false" else OSError("fixture"), True]
    if mode == "exception":
        with pytest.raises(OSError):
            tick(env, NOW + 1001)
    else:
        tick(env, NOW + 1001)
    assert env.save.call_count == 2
    assert env.identity[FIELD]["status"] == "sent"
    assert (CHAT, ROOT) in env.identity["pending_tasks"]
    env.save.side_effect = None
    tick(env, NOW + 1062)
    assert env.identity[FIELD]["status"] == "complete"
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    env.send.assert_awaited_once()


def finalize(env, *, log=False):
    return runtime._finalize_game_command_sent(
        COMMAND, msg_id=ROOT, sent_at=NOW + .5, send_started_at=NOW,
        send_as_id=ID, game_group_id=CHAT, topic_id=0, track=True, max_retry=0,
        append_sent_log=log, reply_timeout=concubine.CONCUBINE_PHASE_TIMEOUT_SEC,
        send_intent={"op_id": env.identity[FIELD]["op_id"], "source_module": concubine.CONCUBINE_QUERY_SOURCE},
    )


@pytest.mark.parametrize("route", ["native", "passive"])
def test_native_runtime_receipt_and_early_status_bypass_generic_dedupe(env, route, monkeypatch):
    monkeypatch.setattr(app, "_claim_runtime_event", Mock(side_effect=AssertionError("generic dedupe")))
    monkeypatch.setattr(passive_inbox, "_mark_observed_passive_event", Mock(side_effect=AssertionError("passive dedupe")))

    async def sent(*_args, **_kwargs):
        finalize(env)
        assert await reply(env, route=route)
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]


@pytest.mark.parametrize("mode", ["success", "false", "exception"])
def test_late_native_read_registration_cleanup_is_exact_and_transactional(env, mode):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    with state_module.use_identity(ID):
        finalize(env)
    env.identity["pending_tasks"][(CHAT, ROOT + 10)] = dict(
        env.identity["pending_tasks"][(CHAT, ROOT)], message_id=ROOT + 10, op_id="f" * 32)
    before = copy.deepcopy(env.identity)
    if mode == "false":
        env.save.return_value = False
    elif mode == "exception":
        env.save.side_effect = OSError("fixture")
    with state_module.use_identity(ID):
        if mode == "exception":
            with pytest.raises(OSError):
                asyncio.run(concubine._recover_status_query(NOW + 10))
        else:
            assert asyncio.run(concubine._recover_status_query(NOW + 10))
    if mode != "success":
        assert env.identity == before
    else:
        assert (CHAT, ROOT) not in env.identity["pending_tasks"]
        assert (CHAT, ROOT + 10) in env.identity["pending_tasks"]


def test_native_logs_restore_unknown_read_once_after_sqlite_reload(env, monkeypatch, tmp_path):
    env.send.return_value = None
    assert not asyncio.run(start(env))
    op_id = env.identity[FIELD]["op_id"]
    assert persistence.save_state()
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    assert env.identity[FIELD]["op_id"] == op_id
    def stamp(at):
        return datetime.fromtimestamp(at, concubine.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8")

    entries = [
        {"ts": stamp(NOW), "event_type": "sent", "sender_id": ID, "account_id": ACCOUNT,
         "chat_id": CHAT, "message_id": ROOT, "text": COMMAND, "op_id": op_id,
         "source_module": concubine.CONCUBINE_QUERY_SOURCE},
        dict(logged_reply(), ts=stamp(NOW + 1)),
    ]
    path = tmp_path / f"{datetime.fromtimestamp(NOW, concubine.TZ_LOCAL):%Y-%m-%d}.log"
    path.write_text("\n".join(json.dumps(row, ensure_ascii=True) for row in entries) + "\n", encoding="utf-8")
    monkeypatch.setattr(message_log_recovery, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", message_log_recovery.find_recent_message_log_commands)
    monkeypatch.setattr(concubine, "find_message_log_replies", message_log_recovery.find_message_log_replies)
    tick(env, NOW + 10)
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity[FIELD]["msg_id"] == ROOT
    assert env.identity["concubine_voyage_status"] == "idle"
    assert persistence.save_state()
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    assert persistence.load_state()
    assert state_module.get_identity_state(ID)[FIELD]["status"] == "complete"
    env.send.assert_awaited_once()


@pytest.mark.parametrize("explicit", [True, False])
def test_early_unbound_passive_status_stays_replayable_until_exact_receipt(env, explicit):
    env.send.return_value = None
    assert not asyncio.run(start(env))
    context = {"family": "concubine_voyage", "root_msg_id": ROOT, "reply_to_msg_id": ROOT,
               "reply_to_command": COMMAND}
    if explicit:
        context["send_as_id"] = ID
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, route="passive", context=context))
    assert env.identity == before
    assert not passive_inbox._observed_passive_events
    receipt(env)
    assert asyncio.run(reply(env, route="passive", context=context))
    assert env.identity[FIELD]["status"] == "complete"


@pytest.mark.parametrize("ambiguous", [True, False])
def test_passive_voyage_query_without_hint_requires_unique_chat_owner(env, ambiguous):
    assert asyncio.run(start(env))
    state_module.set_identity_account(ID + 1, ACCOUNT + 1)
    other = state_module.get_identity_state(ID + 1)
    other.update(copy.deepcopy(env.identity))
    other[FIELD].update(identity_id=ID + 1, account_id=ACCOUNT + 1, chat_id=CHAT if ambiguous else CHAT - 1)
    before = copy.deepcopy(other)
    assert asyncio.run(reply(env, route="passive", context={
        "family": "concubine_voyage", "root_msg_id": ROOT, "reply_to_msg_id": ROOT,
    })) is not ambiguous
    assert other == before


@pytest.mark.parametrize("record", [{}, None, {"bad": True}])
def test_legacy_or_invalid_voyage_status_query_is_held_and_visible(env, record):
    env.identity.update(concubine_phase="voyage_status_pending", concubine_status_query=record)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 1001)
        asyncio.run(concubine.run_concubine_scheduler(NOW + 1001))
        assert "\u7b49\u5f85\u6838\u5bf9" in concubine.get_concubine_status_text()
    assert env.identity == before
    env.send.assert_not_awaited()


def test_voyage_query_does_not_change_tianxing_or_world_boss_controls(env):
    watched = {key: copy.deepcopy(value) for key, value in env.identity.items()
               if key.startswith(("tianxing_", "world_boss_", "small_world_", "deep_retreat_"))}
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, RETURNED))
    assert {key: env.identity[key] for key in watched} == watched


@pytest.mark.parametrize("kind", ["status", "gift_status", "fragment", "voyage_status"])
@pytest.mark.parametrize("corrupt_pending", [False, True])
def test_queued_status_query_rechecks_new_business_pending_before_dispatch(env, kind, corrupt_pending):
    env.identity["concubine_tianji_enabled"] = True
    if kind == "fragment":
        with state_module.use_identity(ID):
            concubine._set_fragment_progress("xutian", 4, 4)

    async def sent(_command, **kwargs):
        assert kwargs["operation_check"]()
        env.identity["pending_tasks"] = None if corrupt_pending else {
            (CHAT, ROOT + 100): {"cmd": concubine.CMD_CONCUBINE_HEART, "family": "concubine_heart", "chat_id": CHAT},
        }
        assert not kwargs["operation_check"]()
        return None

    env.send.side_effect = sent
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine._send_status_query(kind, NOW))


@pytest.mark.parametrize("text", [LOCK, NO_SETTLEMENT])
def test_restart_does_not_slide_saved_voyage_probe_deadline(env, text):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, text))
    due = env.identity["next_concubine_time"]
    for at in (NOW + 30, NOW + 1000, due + 100):
        with state_module.use_identity(ID):
            concubine.restore_concubine_runtime(at)
        assert env.identity["next_concubine_time"] == due
    set_receipt(env, ROOT + 10, due + 100)
    tick(env, due + 100)
    assert env.send.await_args.args == (COMMAND,)
    assert env.send.await_count == 2
