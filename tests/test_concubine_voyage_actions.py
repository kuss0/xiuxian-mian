import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, message_log_recovery, persistence, runtime, state as state_module
from model.features import _phaseful, concubine, passive_inbox
from tests import test_concubine_fragment_actions as fragment_tests
from tests.test_concubine_fragment_actions import ACCOUNT, BOT, CHAT, ID, NAME, NOW, ROOT


FIELD = "concubine_voyage_actions"
base_env = fragment_tests.env
SOURCE = "concubine_voyage"
KINDS = ("voyage", "voyage_return")
ROUTE = concubine.CONCUBINE_VOYAGE_DEFAULT_ROUTE
COMMANDS = {
    "voyage": f"{concubine.CMD_CONCUBINE_VOYAGE} {ROUTE}",
    "voyage_return": concubine.CMD_CONCUBINE_VOYAGE_RETURN,
}
STARTED = (
    "\u3010\u4e71\u661f\u6d77\u8fdc\u822a\u00b7\u542f\u3011\n"
    f"\u4f60\u547d\u4f8d\u59be\u3010{NAME}\u3011\u6cbf {ROUTE} \u822a\u7ebf\u8fdc\u884c\u3002\n"
    "\u9884\u8ba1\u5f52\u822a\u65f6\u95f4\uff1a12\u5c0f\u65f6\u540e\u3002"
)
RETURNED = (
    "\u3010\u4e71\u661f\u6d77\u8fdc\u822a\u00b7\u5f52\u3011\n"
    f"\u4f8d\u59be\u3010{NAME}\u3011\u5df2\u81ea {ROUTE} \u822a\u7ebf\u5f52\u6765\uff0c\u5411\u4f60\u5448\u4e0a\u6536\u83b7\uff1a\n"
    "- \u4fee\u4e3a +405\n- \u7075\u77f3 +97\n- \u517b\u9b42\u6728 x4\n"
    "\u8def\u9047\u98ce\u66b4\uff0c\u4f8d\u59be\u9053\u5fc3\u53d7\u60ca\uff0c\u60c5\u7f18\u51cf\u5c11 32 \u70b9\u3002"
)


@pytest.fixture
def env(base_env, monkeypatch):
    base_env.identity.update(
        concubine_enabled=False, concubine_tianji_enabled=False,
        concubine_heart_enabled=False, concubine_voyage_enabled=True,
        concubine_voyage_status="idle", concubine_voyage_route=ROUTE,
        concubine_affinity=320,
    )
    if hasattr(concubine, "voyage_actions"):
        monkeypatch.setattr(concubine.voyage_actions, "_INFLIGHT", {})
    return base_env


def prepare(env, kind):
    if kind == "voyage_return":
        env.identity.update(concubine_voyage_status="returned", concubine_voyage_return_at=NOW - 1)


async def start(env, kind):
    with state_module.use_identity(ID):
        return await getattr(concubine, f"_send_{kind}_command")(env.clock[0])


def receipt(env, kind, *, root=ROOT):
    record = env.identity[FIELD][kind]
    env.identity["pending_tasks"][(CHAT, root)] = {
        "cmd": record["command"], "family": "concubine_voyage",
        "op_id": record["op_id"], "source_module": SOURCE,
        "account_id": ACCOUNT, "chat_id": CHAT, "message_id": root,
        "sent_at": env.clock[0] + .5, "send_started_at": env.clock[0],
    }


async def reply(env, kind, *, text=None, route="direct", context=None, root=ROOT, at=NOW + 1, chat=CHAT):
    text = (STARTED if kind == "voyage" else RETURNED) if text is None else text
    parent = SimpleNamespace(id=root, chat_id=chat, sender_id=ID, raw_text=COMMANDS[kind])
    event = SimpleNamespace(id=root + 1, chat_id=chat, sender_id=BOT, server_event_at=at)
    if context is None:
        context = {"send_as_id": ID, "account_id": ACCOUNT, "chat_id": chat, "family": "concubine_voyage",
                   "root_msg_id": root, "reply_to_msg_id": root, "sender_id": BOT}
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


@pytest.mark.parametrize("kind", KINDS)
def test_voyage_intent_is_saved_before_tracked_zero_retry_dispatch(env, kind):
    prepare(env, kind)

    async def sent(command, **kwargs):
        record = env.identity[FIELD][kind]
        assert record["status"] == "sending"
        assert (record["identity_id"], record["account_id"], record["chat_id"]) == (ID, ACCOUNT, CHAT)
        assert record["command"] == command == COMMANDS[kind]
        assert kwargs["track"] is True and kwargs["max_retry"] == 0
        assert kwargs["source_module"] == SOURCE and kwargs["op_id"] == record["op_id"]
        assert kwargs["send_as_id"] == ID and kwargs["target_chat_id"] == CHAT
        assert kwargs["operation_check"]()
        env.save.assert_called()
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "sent"


@pytest.mark.parametrize("kind", KINDS)
def test_early_voyage_completion_is_not_rewound_by_send_return(env, kind):
    prepare(env, kind)

    async def sent(*_args, **_kwargs):
        receipt(env, kind)
        assert await reply(env, kind)
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "complete"
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["concubine_voyage_msg_id"] == 0
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", ["none", "exception", "cancel"])
def test_unknown_voyage_survives_restart_without_resend(env, kind, mode):
    prepare(env, kind)
    env.send.return_value = None
    if mode != "none":
        env.send.side_effect = RuntimeError("fixture") if mode == "exception" else asyncio.CancelledError()
        with pytest.raises(RuntimeError if mode == "exception" else asyncio.CancelledError):
            asyncio.run(start(env, kind))
    else:
        assert not asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "unknown"
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 1000)
    assert env.identity == before
    env.send.side_effect = None
    env.clock[0] = NOW + 100000
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
    assert not asyncio.run(start(env, kind))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_voyage_send_cannot_write_through_replaced_owner(env, kind, change):
    prepare(env, kind)
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
    assert not asyncio.run(start(env, kind))
    assert state_module._meta_state == after


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_unowned_scalar_voyage_reply_cannot_complete(env, kind, route):
    prepare(env, kind)
    env.identity.update(concubine_phase=kind + "_pending", concubine_voyage_msg_id=ROOT)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind, route=route))
    assert env.identity == before


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_voyage_return_affinity_loss_is_applied_once(env, route):
    prepare(env, "voyage_return")
    assert asyncio.run(start(env, "voyage_return"))
    assert asyncio.run(reply(env, "voyage_return", route=route))
    assert env.identity["concubine_affinity"] == 288
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, "voyage_return", route=route))
    assert env.identity == before
    env.audit.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
def test_unrecognized_voyage_reply_retains_original_pending(env, kind):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind, text="unrecognized fixture reply"))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
def test_failed_voyage_completion_save_rolls_back_for_replay(env, kind):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    receipt(env, kind)
    before = copy.deepcopy(env.identity)
    env.save.return_value = False
    assert not asyncio.run(reply(env, kind))
    assert env.identity == before
    env.audit.assert_not_awaited()
    env.save.return_value = True
    assert asyncio.run(reply(env, kind))
    assert env.identity[FIELD][kind]["status"] == "complete"


@pytest.mark.parametrize("kind", KINDS)
def test_legacy_voyage_timeout_cannot_send_a_second_mutation(env, kind):
    prepare(env, kind)
    env.identity.update(concubine_phase=kind + "_pending", concubine_voyage_msg_id=ROOT,
                        next_concubine_time=NOW - 1000)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(NOW))
    env.send.assert_not_awaited()
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["pause", "identity", "partner", "affinity", "timer", "chat", "voyage",
                                    "query", "fragments", "gifts", "pending", "invalid_pending"])
def test_queued_voyage_rechecks_controls_plan_and_sibling_work(env, kind, change, monkeypatch):
    prepare(env, kind)
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(_command, **kwargs):
        if change == "pause":
            state_module.set_global_enabled(False)
        elif change == "identity":
            state_module.set_identity_enabled(ID, False)
        elif change == "partner":
            env.identity["concubine_name"] = "replacement"
        elif change == "affinity":
            env.identity["concubine_affinity"] = 0
        elif change == "timer":
            env.identity["next_concubine_time"] = NOW + 9999
        elif change == "chat":
            state_module.set_game_group_id(CHAT - 1)
        elif change == "voyage":
            env.identity["concubine_voyage_return_at"] = NOW + 9999
        elif change == "query":
            env.identity["concubine_status_query"] = {"invalid": True}
        elif change == "fragments":
            env.identity["concubine_fragment_actions"] = {"invalid": True}
        elif change == "gifts":
            env.identity["concubine_gift_actions"] = {"invalid": True}
        elif change == "pending":
            env.identity["pending_tasks"][(CHAT, ROOT + 50)] = {"cmd": concubine.CMD_CONCUBINE_HEART, "family": "concubine_heart"}
        else:
            env.identity["pending_tasks"][(CHAT, ROOT + 50)] = None
        assert not kwargs["operation_check"]()
        block.update(status="unsent", code="pre_send_guard", at=NOW)
        return None

    env.send.side_effect = sent
    assert not asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "unsent"
    assert "result" not in env.identity[FIELD][kind]
    if change == "timer":
        assert env.identity["next_concubine_time"] == NOW + 9999


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", ["false", "exception"])
def test_voyage_intent_save_failure_prevents_transport(env, kind, mode):
    prepare(env, kind)
    before = copy.deepcopy(env.identity)
    env.save.return_value = False
    if mode == "exception":
        env.save.side_effect = OSError("fixture save")
        with pytest.raises(OSError):
            asyncio.run(start(env, kind))
    else:
        assert not asyncio.run(start(env, kind))
    assert env.identity == before
    env.send.assert_not_awaited()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("fresh", [True, False])
def test_only_fresh_definitely_unsent_evidence_permits_voyage_retry(env, kind, fresh, monkeypatch):
    prepare(env, kind)
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(*_args, **_kwargs):
        block.update(status="unsent", code="send_queue_timeout", at=NOW if fresh else NOW - 100)
        return None

    env.send.side_effect = sent
    assert not asyncio.run(start(env, kind))
    record = env.identity[FIELD][kind]
    assert record["status"] == ("unsent" if fresh else "unknown")
    assert not asyncio.run(start(env, kind))
    env.send.assert_awaited_once()
    if fresh:
        env.clock[0] = record["retry_at"]
        env.send.side_effect = None
        env.send.return_value = SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=env.clock[0], send_started_at=env.clock[0])
        assert asyncio.run(start(env, kind))
        assert env.identity[FIELD][kind]["op_id"] != record["op_id"]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field,value", [("id", True), ("id", "88001"), ("chat_id", 0), ("chat_id", CHAT - 1),
                                       ("sent_at", NOW - 100), ("sent_at", NOW + 100), ("send_started_at", 0)])
def test_malformed_voyage_receipt_is_retained_as_unknown(env, kind, field, value):
    prepare(env, kind)
    setattr(env.send.return_value, field, value)
    assert not asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "unknown"
    assert env.identity["concubine_voyage_msg_id"] == 0


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("route", ["direct", "native", "passive"])
@pytest.mark.parametrize("field,value", [("account_id", ACCOUNT + 1), ("chat_id", CHAT - 1),
                                       ("root_msg_id", ROOT + 1), ("reply_to_msg_id", ROOT + 1),
                                       ("op_id", "foreign"), ("source_module", "foreign"),
                                       ("reply_to_command", ".foreign"), ("reply_to_command_edited", True)])
def test_foreign_voyage_context_cannot_complete_or_consume(env, kind, route, field, value):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    context = {"send_as_id": ID, "account_id": ACCOUNT, "chat_id": CHAT, "family": "concubine_voyage",
               "root_msg_id": ROOT, "reply_to_msg_id": ROOT, "sender_id": BOT}
    context[field] = value
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind, route=route, context=context))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["partner", "route", "duplicate_header", "prefix", "summary"])
def test_review_incomplete_or_conflicting_voyage_text_cannot_complete(env, kind, change):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    text = STARTED if kind == "voyage" else RETURNED
    if change == "partner":
        text = text.replace(NAME, "foreign")
    elif change == "route":
        text = text.replace(ROUTE, "foreign")
    elif change == "duplicate_header":
        text = text.splitlines()[0] + "\n" + text
    elif change == "prefix":
        text = "quoted example, not a settlement:\n" + text
    else:
        text = text.splitlines()[0]
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind, text=text))
    assert env.identity == before


@pytest.mark.parametrize("change", ["no_rewards", "double_loss", "malformed_loss"])
def test_review_return_text_needs_complete_unambiguous_settlement(env, change):
    prepare(env, "voyage_return")
    assert asyncio.run(start(env, "voyage_return"))
    if change == "no_rewards":
        text = "\n".join(RETURNED.splitlines()[:2])
    elif change == "double_loss":
        text = RETURNED + "\n" + RETURNED.splitlines()[-1]
    else:
        text = RETURNED.replace("32", "3,,2")
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, "voyage_return", text=text))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("change", ["partner", "snapshot", "voyage"])
def test_late_voyage_fact_cannot_overwrite_replacement_snapshot(env, kind, change):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    if change == "partner":
        env.identity["concubine_name"] = "replacement"
    elif change == "snapshot":
        env.identity["concubine_last_snapshot_at"] = NOW + 1
    else:
        env.identity["concubine_voyage_return_at"] = NOW + 99999
    env.identity["next_concubine_time"] = NOW + 11111
    before = copy.deepcopy(env.identity)
    assert asyncio.run(reply(env, kind))
    result = env.identity[FIELD][kind]["result"]
    assert result["applied"] is False and result["affinity_applied"] is False
    for key in ("concubine_name", "concubine_affinity", "concubine_last_snapshot_at", "concubine_voyage_status",
                "concubine_voyage_return_at", "next_concubine_time"):
        assert env.identity[key] == before[key]


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", ["false", "exception"])
def test_voyage_recovery_checkpoint_failure_rolls_back(env, kind, mode, monkeypatch):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    scan = Mock(return_value=[])
    monkeypatch.setattr(concubine, "_find_owned_concubine_replies", scan)
    before = copy.deepcopy(env.identity)
    env.save.return_value = False
    with state_module.use_identity(ID):
        if mode == "exception":
            env.save.side_effect = OSError("fixture")
            with pytest.raises(OSError):
                asyncio.run(concubine.voyage_actions.recover(NOW + 10))
        else:
            assert asyncio.run(concubine.voyage_actions.recover(NOW + 10))
    assert env.identity == before
    scan.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
def test_voyage_log_recovery_uses_server_clock_and_exact_pending(env, kind, monkeypatch):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    receipt(env, kind)
    env.identity["pending_tasks"][(CHAT - 1, ROOT)] = {"cmd": COMMANDS[kind], "chat_id": CHAT - 1}
    entry = {"message_id": ROOT + 1, "chat_id": CHAT, "reply_to_msg_id": ROOT, "event_type": "message",
             "sender_id": BOT, "sender_is_bot": True, "server_event_at": NOW + 1,
             "text": STARTED if kind == "voyage" else RETURNED}
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[entry]))
    env.clock[0] = NOW + 10000
    with state_module.use_identity(ID):
        assert asyncio.run(concubine.voyage_actions.recover(env.clock[0]))
    record = env.identity[FIELD][kind]
    assert record["status"] == "complete" and record["reply_at"] == NOW + 1
    if kind == "voyage":
        assert env.identity["concubine_voyage_return_at"] == NOW + 1 + 12 * 3600 + concubine.CD_BUFFER_SEC
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert (CHAT - 1, ROOT) in env.identity["pending_tasks"]
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("legacy", [False, True])
def test_review_shared_summary_replay_cannot_bypass_voyage_owner(env, kind, legacy, monkeypatch):
    prepare(env, kind)
    if legacy:
        env.identity.update(concubine_phase=kind + "_pending", concubine_voyage_msg_id=ROOT)
    else:
        assert asyncio.run(start(env, kind))
        receipt(env, kind)
    monkeypatch.setattr(_phaseful.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(_phaseful, "send_game_command", env.send)
    monkeypatch.setattr(_phaseful, "send_audit_log", env.audit)
    monkeypatch.setattr(_phaseful, "save_state", env.save)
    before = copy.deepcopy(env.identity)
    env.send.reset_mock()
    asyncio.run(_phaseful._replay_summary_consumed_command(ID, {
        "cmd": COMMANDS[kind], "chat_id": CHAT, "msg_id": ROOT, "sent_at": NOW,
        "track": not legacy, "max_retry": 0,
    }))
    env.send.assert_not_awaited()
    assert env.identity == before


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_positive_voyage_affinity_is_owned_and_idempotent(env, route):
    prepare(env, "voyage_return")
    text = RETURNED.replace("\u60c5\u7f18\u51cf\u5c11 32", "\u60c5\u7f18\u589e\u52a0 6")
    assert asyncio.run(start(env, "voyage_return"))
    assert asyncio.run(reply(env, "voyage_return", text=text, route=route))
    assert env.identity["concubine_affinity"] == 326
    assert not asyncio.run(reply(env, "voyage_return", text=text, route=route))
    assert env.identity["concubine_affinity"] == 326


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("wait", ["", "\uff0c\u9884\u8ba1\u5f52\u822a\u8fd8\u9700 56 \u5206\u949f"])
def test_explicit_voyage_refusal_completes_only_original_action(env, kind, wait):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    assert asyncio.run(reply(env, kind, text="\u4f8d\u59be\u5c1a\u672a\u5f52\u822a" + wait + "\u3002"))
    assert env.identity[FIELD][kind]["status"] == "complete"
    assert env.identity["concubine_affinity"] == 320
    assert env.identity["concubine_voyage_status"] == "sailing"
    due = NOW + 1 + 56 * 60 + concubine.CD_BUFFER_SEC if wait else 0
    assert env.identity["concubine_voyage_return_at"] == due
    assert not asyncio.run(start(env, "voyage"))
    env.audit.assert_not_awaited()
    if not wait:
        env.clock[0] = env.identity["next_concubine_time"]
        env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=env.clock[0], send_started_at=env.clock[0])
        with state_module.use_identity(ID):
            asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
        assert env.send.await_args.args == (concubine.CMD_CONCUBINE_VOYAGE_STATUS,)


@pytest.mark.parametrize("kind", KINDS)
def test_no_partner_voyage_refusal_reads_partner_without_spending_again(env, kind):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    assert asyncio.run(reply(env, kind, text="\u4f60\u5c1a\u65e0\u4f8d\u59be\u3002"))
    assert env.identity["concubine_availability"] == "unknown"
    env.clock[0] = env.identity["next_concubine_time"]
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=env.clock[0], send_started_at=env.clock[0])
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
    assert env.send.await_args.args == (concubine.CMD_CONCUBINE_STATUS,)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", ["false", "exception"])
@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_voyage_failed_completion_is_replayable_on_every_route(env, kind, mode, route):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    receipt(env, kind)
    before = copy.deepcopy(env.identity)
    if mode == "exception":
        env.save.side_effect = OSError("fixture")
        with pytest.raises(OSError):
            asyncio.run(reply(env, kind, route=route))
    else:
        env.save.return_value = False
        assert not asyncio.run(reply(env, kind, route=route))
    assert env.identity == before
    env.audit.assert_not_awaited()
    env.save.side_effect = None
    env.save.return_value = True
    assert asyncio.run(reply(env, kind, route=route))
    assert env.identity[FIELD][kind]["status"] == "complete"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field,value", [("kind", "other"), ("identity_id", ID + 1), ("account_id", True),
                                       ("chat_id", False), ("command", ".foreign"), ("op_id", ""), ("plan_key", ""),
                                       ("snapshot_at", NOW + 1), ("affinity", False), ("msg_id", True),
                                       ("voyage", {}), ("status", []), ("result", {})])
def test_corrupt_voyage_record_stays_visible_without_guessing(env, kind, field, value):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    env.identity[FIELD][kind][field] = value
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert concubine.voyage_actions.records() is None
        assert concubine.voyage_actions.block_reason() == "invalid"
        assert asyncio.run(concubine.voyage_actions.recover(NOW + 86400))
        concubine.restore_concubine_runtime(NOW + 86400)
    assert not asyncio.run(start(env, kind))
    assert not asyncio.run(reply(env, kind))
    assert env.identity == before


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("state", ["sent", "unknown", "complete"])
def test_voyage_action_and_receipt_survive_sqlite_reload(env, kind, state):
    prepare(env, kind)
    if state == "unknown":
        env.send.return_value = None
    asyncio.run(start(env, kind))
    receipt(env, kind)
    if state == "complete":
        assert asyncio.run(reply(env, kind))
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    assert env.identity[FIELD][kind]["status"] == state
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 60)
    if state != "complete":
        assert asyncio.run(reply(env, kind))
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind))
    assert env.identity == before


def finalize(env, kind, *, log=False):
    return runtime._finalize_game_command_sent(
        COMMANDS[kind], msg_id=ROOT, sent_at=NOW + .5, send_started_at=NOW,
        send_as_id=ID, game_group_id=CHAT, topic_id=0, track=True, max_retry=0,
        append_sent_log=log, reply_timeout=concubine.CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC,
        send_intent={"op_id": env.identity[FIELD][kind]["op_id"], "source_module": SOURCE},
    )


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("route", ["native", "passive"])
def test_native_voyage_receipt_and_late_registration_do_not_rearm_pending(env, kind, route):
    prepare(env, kind)

    async def sent(*_args, **_kwargs):
        message = finalize(env, kind)
        assert "account_id" not in env.identity["pending_tasks"][(CHAT, ROOT)]
        assert await reply(env, kind, route=route)
        finalize(env, kind)
        return message

    env.send.side_effect = sent
    assert asyncio.run(start(env, kind))
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert env.identity[FIELD][kind]["status"] == "complete"


@pytest.mark.parametrize("kind", KINDS)
def test_unknown_voyage_native_log_recovers_after_reload_without_resending(env, kind, monkeypatch, tmp_path):
    prepare(env, kind)
    monkeypatch.setattr(runtime, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(message_log_recovery, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", message_log_recovery.find_recent_message_log_commands)
    monkeypatch.setattr(concubine, "find_message_log_replies", message_log_recovery.find_message_log_replies)
    env.send.return_value = None
    assert not asyncio.run(start(env, kind))
    with state_module.use_identity(ID):
        finalize(env, kind, log=True)
    env.identity["pending_tasks"] = {}
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    path = tmp_path / (datetime.fromtimestamp(NOW, concubine.TZ_LOCAL).strftime("%Y-%m-%d") + ".log")
    entry = {
        "ts": datetime.fromtimestamp(NOW + 1, concubine.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "event_type": "message", "sender_id": BOT, "sender_is_bot": True, "chat_id": CHAT,
        "message_id": ROOT + 1, "reply_to_msg_id": ROOT, "server_event_at": NOW + 1,
        "text": STARTED if kind == "voyage" else RETURNED,
    }
    with path.open("a") as log:
        log.write(json.dumps(entry) + "\n")
    with state_module.use_identity(ID):
        assert asyncio.run(concubine.voyage_actions.recover(NOW + 100))
    assert env.identity[FIELD][kind]["status"] == "complete"
    env.send.assert_awaited_once()


def test_full_voyage_launch_wait_settlement_cycle(env):
    assert asyncio.run(start(env, "voyage"))
    assert asyncio.run(reply(env, "voyage"))
    assert not asyncio.run(start(env, "voyage_return"))
    assert not asyncio.run(start(env, "voyage"))
    env.clock[0] = env.identity["next_concubine_time"]
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=env.clock[0], send_started_at=env.clock[0])
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
    assert env.send.await_args.args == (concubine.CMD_CONCUBINE_VOYAGE_RETURN,)
    assert asyncio.run(reply(env, "voyage_return", root=ROOT + 10, at=env.clock[0] + 1))
    assert env.identity["concubine_voyage_status"] == "idle"
    assert env.identity["concubine_affinity"] == 288
    assert not asyncio.run(start(env, "voyage_return"))
    with state_module.use_identity(ID):
        assert all(item["status"] == "complete" for item in concubine.voyage_actions.records().values())
    env.audit.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("anchor", [0, False, ROOT + 100])
def test_review_late_voyage_preserves_replacement_phase(env, kind, anchor):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    receipt(env, kind)
    env.identity.update(concubine_voyage_msg_id=anchor,
                        concubine_last_snapshot_at=NOW + 1, next_concubine_time=NOW + 9999)
    assert asyncio.run(reply(env, kind))
    assert env.identity[FIELD][kind]["status"] == "complete"
    assert env.identity["concubine_phase"] == kind + "_pending"
    assert env.identity["concubine_voyage_msg_id"] is anchor
    assert env.identity["next_concubine_time"] == NOW + 9999
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]


@pytest.mark.parametrize("kind", KINDS)
def test_review_unsent_voyage_preserves_replacement_phase(env, kind, monkeypatch):
    prepare(env, kind)
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(_command, **kwargs):
        env.identity["next_concubine_time"] = NOW + 9999
        assert not kwargs["operation_check"]()
        block.update(status="unsent", code="pre_send_guard", at=NOW)
        return None

    env.send.side_effect = sent
    assert not asyncio.run(start(env, kind))
    assert env.identity[FIELD][kind]["status"] == "unsent"
    assert env.identity["concubine_phase"] == kind + "_pending"
    assert env.identity["next_concubine_time"] == NOW + 9999


@pytest.mark.parametrize("value", [False, True, "0", None, [], {}, float("inf"), float("nan")])
def test_review_completed_voyage_rejects_invalid_result_timestamp(env, value):
    prepare(env, "voyage_return")
    assert asyncio.run(start(env, "voyage_return"))
    assert asyncio.run(reply(env, "voyage_return"))
    env.identity[FIELD]["voyage_return"]["result"]["voyage"]["return_at"] = value
    with state_module.use_identity(ID):
        assert concubine.voyage_actions.records() is None
        assert concubine.voyage_actions.block_reason() == "invalid"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("unknown", [False, True])
def test_passive_voyage_without_identity_hint_requires_unique_receipt(env, kind, unknown):
    prepare(env, kind)
    if unknown:
        env.send.return_value = None
    asyncio.run(start(env, kind))
    receipt(env, kind)
    context = {"family": "concubine_voyage", "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    assert asyncio.run(reply(env, kind, route="passive", context=context))
    assert env.identity[FIELD][kind]["status"] == "complete"
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", KINDS)
def test_passive_voyage_rejects_ambiguous_owner_without_dedupe(env, kind):
    prepare(env, kind)
    assert asyncio.run(start(env, kind))
    state_module.set_identity_account(ID + 1, ACCOUNT)
    other = state_module.get_identity_state(ID + 1)
    other.update(copy.deepcopy(env.identity))
    other[FIELD][kind]["identity_id"] = ID + 1
    context = {"family": "concubine_voyage", "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    before = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(reply(env, kind, route="passive", context=context))
    assert state_module._meta_state == before
    state_module.remove_identity(ID + 1)
    assert asyncio.run(reply(env, kind, route="passive", context=context))


@pytest.mark.parametrize("mode", ["exception", "cancel"])
def test_voyage_audit_failure_cannot_undo_or_repeat_settlement(env, mode):
    prepare(env, "voyage_return")
    assert asyncio.run(start(env, "voyage_return"))
    receipt(env, "voyage_return")
    env.audit.side_effect = OSError("fixture audit") if mode == "exception" else asyncio.CancelledError()
    if mode == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(reply(env, "voyage_return"))
    else:
        assert asyncio.run(reply(env, "voyage_return"))
    assert env.identity[FIELD]["voyage_return"]["status"] == "complete"
    assert env.identity["concubine_affinity"] == 288
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, "voyage_return"))
    assert env.identity == before
    env.audit.assert_awaited_once()
