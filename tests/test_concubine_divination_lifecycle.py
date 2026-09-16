import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from model import app, message_log_recovery, persistence, runtime, state as state_module
from model.features import concubine, passive_inbox
from tests import test_concubine_fragment_actions as fragment_tests
from tests.test_concubine_fragment_actions import ACCOUNT, BOT, CHAT, ID, NAME, NOW, ROOT


base_env = fragment_tests.env
FIELD = "concubine_tianji_action"
COMMAND = concubine.CMD_CONCUBINE_TIANJI
CHAIN = "\u6b8b\u56fe\u5f15\u8def"
SUCCESS = (
    "\u3010\u5929\u673a\u4ee3\u535c\u94fe\u3011\n"
    f"\u4f8d\u59be\u3010{NAME}\u3011\u711a\u9999\u63a8\u6f14\uff0c\u4e3a\u4f60\u63a5\u5f15\u4e00\u7f15\u5929\u673a\u3002\n"
    f"\u5f97\u5366\u3010{CHAIN}\u3011\uff1a\u4e0b\u4e00\u6b21 .\u5165\u68a6\u5bfb\u56fe \u7684\u6b8b\u56fe\u7247\u6bb5\u6389\u7387\u5927\u5e45\u63d0\u5347\u3002\n"
    "\u672c\u6b21\u6d88\u8017\uff1a180\u4fee\u4e3a\u3002"
)
COOLDOWN = "\u5929\u673a\u94fe\u8def\u5c1a\u672a\u91cd\u94f8\uff0c\u8bf7\u5728 24 \u79d2\u540e\u518d\u8bd5\u3002"


@pytest.fixture
def env(base_env, monkeypatch):
    base_env.identity.update(concubine_enabled=False, concubine_tianji_enabled=True,
                             concubine_heart_enabled=False, concubine_voyage_enabled=False,
                             concubine_affinity=320, concubine_tianji_due_at=NOW - 1)
    if hasattr(concubine, "divination_actions"):
        monkeypatch.setattr(concubine.divination_actions, "_INFLIGHT", {})
    return base_env


async def start(env):
    with state_module.use_identity(ID):
        return await concubine._send_tianji_command(env.clock[0])


def receipt(env, *, log=False):
    item = env.identity[FIELD]
    with state_module.use_identity(ID):
        return runtime._finalize_game_command_sent(
            COMMAND, msg_id=ROOT, sent_at=env.clock[0] + .5, send_started_at=env.clock[0],
            send_as_id=ID, game_group_id=CHAT, topic_id=0, track=True, max_retry=0,
            append_sent_log=log, send_intent={"op_id": item["op_id"], "source_module": "concubine_tianji"},
        )


async def reply(env, text=SUCCESS, *, route="direct", context=None, at=NOW + 1):
    parent = SimpleNamespace(id=ROOT, chat_id=CHAT, sender_id=ID, raw_text=COMMAND)
    event = SimpleNamespace(id=ROOT + 1, chat_id=CHAT, sender_id=BOT, server_event_at=at)
    if context is None:
        context = {"send_as_id": ID, "account_id": ACCOUNT, "chat_id": CHAT,
                   "family": "concubine_tianji", "root_msg_id": ROOT, "reply_to_msg_id": ROOT, "sender_id": BOT}
    now = max(env.clock[0], at) + 1
    if route == "native":
        return await app._handle_routed_reply_event(event, text, now, parent, context)
    if route == "passive":
        return await passive_inbox.handle_passive_module_card(
            text, now=now, reply_context=context, event=event, event_type="message")
    with state_module.use_identity(ID):
        return await concubine.handle_concubine_tianji_reply(
            text, now, parent, matched_family="concubine_tianji", current_msg_id=ROOT + 1,
            current_chat_id=CHAT, observed_at=at, reply_context=context)


def test_divination_saves_owned_intent_before_zero_retry_transport(env):
    before_due = env.identity["concubine_tianji_due_at"]

    async def sent(command, **kwargs):
        record = env.identity[FIELD]
        assert record["status"] == "sending" and record["command"] == command == COMMAND
        assert (record["identity_id"], record["account_id"], record["chat_id"]) == (ID, ACCOUNT, CHAT)
        assert kwargs["track"] is True and kwargs["max_retry"] == 0
        assert kwargs["source_module"] == "concubine_tianji" and kwargs["op_id"] == record["op_id"]
        assert kwargs["operation_check"]()
        env.save.assert_called()
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "sent"
    assert env.identity["concubine_tianji_due_at"] == before_due


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_divination_early_completion_survives_late_send_receipt(env, route):
    async def sent(*_args, **_kwargs):
        message = receipt(env)
        assert await reply(env, route=route)
        receipt(env)
        return message

    env.send.side_effect = sent
    assert asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_tianji_chain"] == CHAIN
    assert env.identity["concubine_phase"] == "idle"
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, route=route))
    assert env.identity == before


@pytest.mark.parametrize("mode", ["none", "exception", "cancel"])
def test_unknown_divination_cannot_retry_or_invent_cooldown_after_restart(env, mode):
    env.send.return_value = None
    if mode == "none":
        assert not asyncio.run(start(env))
    else:
        env.send.side_effect = OSError("fixture") if mode == "exception" else asyncio.CancelledError()
        with pytest.raises(OSError if mode == "exception" else asyncio.CancelledError):
            asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unknown"
    assert env.identity["concubine_tianji_due_at"] == NOW - 1
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 1000)
    assert env.identity == before
    env.send.side_effect = None
    env.clock[0] = NOW + 86400
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_scalar_divination_reply_has_no_mutation_authority(env, route):
    env.identity.update(concubine_phase="tianji_pending", concubine_tianji_msg_id=ROOT)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, route=route))
    assert env.identity == before


@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_late_divination_transport_does_not_write_replacement_owner(env, change):
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


@pytest.mark.parametrize("text", [SUCCESS.splitlines()[0], SUCCESS.replace(NAME, "foreign"),
                                 SUCCESS + "\n" + SUCCESS, "quoted:\n" + SUCCESS,
                                 SUCCESS.replace("180", "1,,80"),
                                 COOLDOWN.split("\uff0c")[0]])
def test_incomplete_or_conflicting_divination_cannot_close_operation(env, text):
    assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, text))
    assert env.identity == before


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_divination_completion_save_failure_is_replayable(env, mode):
    assert asyncio.run(start(env))
    receipt(env)
    before = copy.deepcopy(env.identity)
    if mode == "false":
        env.save.return_value = False
        assert not asyncio.run(reply(env))
    else:
        env.save.side_effect = OSError("fixture save")
        with pytest.raises(OSError):
            asyncio.run(reply(env))
    assert env.identity == before
    env.save.return_value, env.save.side_effect = True, None
    assert asyncio.run(reply(env))


def test_completed_divination_retains_original_cooldown_through_reload(env):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    assert persistence.save_state() and persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    env.identity["concubine_tianji_due_at"] = 0
    env.identity["next_concubine_time"] = NOW
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("change", ["pause", "identity", "module", "partner", "affinity", "snapshot",
                                    "timer", "chat", "effect", "phase", "voyage", "query", "fragments",
                                    "gifts", "pending", "invalid_pending"])
def test_queued_divination_rechecks_controls_plan_and_sibling_work(env, change, monkeypatch):
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(_command, **kwargs):
        if change == "pause":
            state_module.set_global_enabled(False)
        elif change == "identity":
            state_module.set_identity_enabled(ID, False)
        elif change == "chat":
            state_module.set_game_group_id(CHAT - 1)
        else:
            changes = {
                "module": ("concubine_tianji_enabled", False), "partner": ("concubine_name", "replacement"),
                "affinity": ("concubine_affinity", 0), "snapshot": ("concubine_last_snapshot_at", NOW + 1),
                "timer": ("next_concubine_time", NOW + 9999), "effect": ("concubine_tianji_chain", "replacement"),
                "phase": ("concubine_phase", "heart_pending"),
                "voyage": ("concubine_voyage_actions", {"invalid": True}),
                "query": ("concubine_status_query", {"invalid": True}),
                "fragments": ("concubine_fragment_actions", {"invalid": True}),
                "gifts": ("concubine_gift_actions", {"invalid": True}),
                "pending": ("pending_tasks", {(CHAT, ROOT + 50): {"cmd": concubine.CMD_CONCUBINE_HEART}}),
                "invalid_pending": ("pending_tasks", None),
            }
            key, value = changes[change]
            env.identity[key] = value
        assert not kwargs["operation_check"]()
        block.update(status="unsent", code="pre_send_guard", at=NOW)
        return None

    env.send.side_effect = sent
    assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unsent"
    assert env.identity["concubine_tianji_due_at"] == NOW - 1
    if change == "timer":
        assert env.identity["next_concubine_time"] == NOW + 9999


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_divination_intent_save_failure_prevents_transport(env, mode):
    before = copy.deepcopy(env.identity)
    env.save.return_value = False
    if mode == "exception":
        env.save.side_effect = OSError("fixture save")
        with pytest.raises(OSError):
            asyncio.run(start(env))
    else:
        assert not asyncio.run(start(env))
    assert env.identity == before
    env.send.assert_not_awaited()


@pytest.mark.parametrize("evidence", ["fresh", "old", "future", "reused", "boolean"])
def test_divination_retry_requires_fresh_definitely_unsent_evidence(env, evidence, monkeypatch):
    at = {"fresh": NOW, "old": NOW - 100, "future": NOW + 100, "reused": NOW, "boolean": True}[evidence]
    block = {"status": "unsent", "code": "send_queue_timeout", "at": at} if evidence == "reused" else {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(*_args, **_kwargs):
        block.update(status="unsent", code="send_queue_timeout", at=at)
        return None

    env.send.side_effect = sent
    assert not asyncio.run(start(env))
    record = copy.deepcopy(env.identity[FIELD])
    assert record["status"] == ("unsent" if evidence == "fresh" else "unknown")
    assert env.identity["concubine_tianji_due_at"] == NOW - 1
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()
    if evidence == "fresh":
        env.clock[0] = record["retry_at"]
        env.send.side_effect = None
        env.send.return_value = SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=env.clock[0], send_started_at=env.clock[0])
        assert asyncio.run(start(env))
        assert env.identity[FIELD]["op_id"] != record["op_id"]


@pytest.mark.parametrize("field,value", [("id", True), ("id", "88001"), ("chat_id", 0), ("chat_id", CHAT - 1),
                                       ("sent_at", NOW - 100), ("sent_at", NOW + 100), ("sent_at", float("nan")),
                                       ("send_started_at", 0), ("send_started_at", True)])
def test_malformed_divination_receipt_is_retained_as_unknown(env, field, value):
    setattr(env.send.return_value, field, value)
    assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unknown"
    assert env.identity["concubine_tianji_msg_id"] == 0


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
@pytest.mark.parametrize("field,value", [("account_id", ACCOUNT + 1), ("chat_id", CHAT - 1),
                                       ("root_msg_id", ROOT + 1), ("reply_to_msg_id", ROOT + 1),
                                       ("op_id", "foreign"), ("source_module", "foreign"),
                                       ("reply_to_command", ".foreign"), ("reply_to_command_edited", True)])
def test_foreign_divination_context_cannot_complete_or_consume(env, route, field, value):
    assert asyncio.run(start(env))
    context = {"send_as_id": ID, "account_id": ACCOUNT, "chat_id": CHAT, "family": "concubine_tianji",
               "root_msg_id": ROOT, "reply_to_msg_id": ROOT, "sender_id": BOT, field: value}
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, route=route, context=context))
    assert env.identity == before


@pytest.mark.parametrize("field,value", [("sender_id", BOT + 1), ("sender_id", str(BOT)),
                                       ("sender_id", True), ("family", "concubine_heart"),
                                       ("send_as_id", ID + 1)])
def test_divination_rejects_foreign_direct_identity_and_sender(env, field, value):
    assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, context={field: value}))
    assert env.identity == before


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_divination_recovery_checkpoint_failure_rolls_back(env, mode, monkeypatch):
    assert asyncio.run(start(env))
    scan = Mock(return_value=[])
    monkeypatch.setattr(concubine, "_find_owned_concubine_replies", scan)
    before = copy.deepcopy(env.identity)
    env.save.return_value = False
    with state_module.use_identity(ID):
        if mode == "exception":
            env.save.side_effect = OSError("fixture")
            with pytest.raises(OSError):
                asyncio.run(concubine.divination_actions.recover(NOW + 10))
        else:
            assert asyncio.run(concubine.divination_actions.recover(NOW + 10))
    assert env.identity == before
    scan.assert_not_called()


@pytest.mark.parametrize("paused", ["global", "identity", "module"])
def test_paused_divination_still_accepts_its_terminal_fact_without_rescheduling(env, paused):
    assert asyncio.run(start(env))
    if paused == "global":
        state_module.set_global_enabled(False)
    elif paused == "identity":
        state_module.set_identity_enabled(ID, False)
    else:
        env.identity["concubine_tianji_enabled"] = False
    before_next = env.identity["next_concubine_time"]
    assert asyncio.run(reply(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_tianji_chain"] == CHAIN
    assert env.identity["next_concubine_time"] == before_next
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("change", ["snapshot", "partner", "effect"])
def test_late_divination_retains_evidence_without_rewinding_newer_state(env, change):
    assert asyncio.run(start(env))
    key, value = {"snapshot": ("concubine_last_snapshot_at", NOW + 2),
                  "partner": ("concubine_name", "replacement"),
                  "effect": ("concubine_tianji_chain", "replacement")}[change]
    env.identity[key] = value
    env.identity["next_concubine_time"] = NOW + 9999
    assert asyncio.run(reply(env))
    assert env.identity[FIELD]["result"]["applied"] is False
    assert env.identity[key] == value
    assert env.identity["concubine_tianji_due_at"] == NOW - 1
    assert env.identity["next_concubine_time"] == NOW + 9999


@pytest.mark.parametrize("outcome,text", [
    ("resource_shortage", "\u4fee\u4e3a\u4e0d\u8db3\uff0c\u4ee3\u535c\u5929\u673a\u9700\u6d88\u8017 180 \u4fee\u4e3a\u3002"),
    ("affinity_shortage", "\u60c5\u7f18\u672a\u81f3\uff0c\u65e0\u6cd5\u4e3a\u4f60\u535c\u7b97\u5929\u673a\u3002"),
    ("no_partner", "\u4f60\u5c1a\u65e0\u4f8d\u59be\u3002"),
    ("voyage_lock", "\u4f8d\u59be\u6b63\u5728\u8fdc\u822a\u9014\u4e2d\uff0c\u6682\u65e0\u6cd5\u711a\u9999\u4ee3\u535c\u3002"),
])
def test_divination_refusals_do_not_invent_spending_or_business_cooldown(env, outcome, text):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, text))
    assert env.identity[FIELD]["result"]["outcome"] == outcome
    assert env.identity[FIELD]["result"]["cost"] == 0
    assert env.identity["concubine_tianji_due_at"] == NOW - 1
    assert env.identity["concubine_affinity"] == 320
    assert env.identity["concubine_phase"] == "idle"
    if outcome == "resource_shortage":
        with state_module.use_identity(ID):
            assert concubine.divination_actions.record() is not None
        assert env.identity[FIELD]["retry_at"] > NOW
        assert not asyncio.run(start(env))
    elif outcome in {"no_partner", "affinity_shortage"}:
        assert env.identity["concubine_availability"] == "unknown"
        assert env.identity["concubine_last_snapshot_at"] == 0
    else:
        assert env.identity["concubine_voyage_status"] == "sailing"
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, text))
    assert env.identity == before


@pytest.mark.parametrize("text", [SUCCESS, COOLDOWN])
def test_divination_success_and_cd_schedule_authoritative_clock(env, text):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, text))
    expected = NOW + 1 + (concubine.CONCUBINE_TIANJI_CD_SEC if text == SUCCESS else 24) + concubine.CD_BUFFER_SEC
    assert env.identity["concubine_tianji_due_at"] == expected
    assert env.identity["next_concubine_time"] == expected + 120
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, text))
    assert env.identity == before


def test_divination_completion_releases_due_dream_on_short_chain(env):
    env.identity["concubine_enabled"] = True
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    assert env.identity["next_concubine_time"] == NOW + 2 + 120
    env.clock[0] = env.identity["next_concubine_time"]
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=env.clock[0], send_started_at=env.clock[0])
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
    assert env.send.await_args.args == (concubine.CMD_CONCUBINE_DREAM,)


@pytest.mark.parametrize("unknown", [False, True])
def test_passive_divination_without_identity_hint_requires_unique_receipt(env, unknown):
    if unknown:
        env.send.return_value = None
    asyncio.run(start(env))
    receipt(env)
    context = {"family": "concubine_tianji", "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    assert asyncio.run(reply(env, route="passive", context=context))
    assert env.identity[FIELD]["status"] == "complete"
    env.send.assert_awaited_once()


def test_passive_divination_ambiguous_owner_remains_replayable(env):
    assert asyncio.run(start(env))
    state_module.set_identity_account(ID + 1, ACCOUNT)
    other = state_module.get_identity_state(ID + 1)
    other.update(copy.deepcopy(env.identity))
    other[FIELD]["identity_id"] = ID + 1
    context = {"family": "concubine_tianji", "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    before = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(reply(env, route="passive", context=context))
    assert state_module._meta_state == before
    state_module.remove_identity(ID + 1)
    assert asyncio.run(reply(env, route="passive", context=context))


def test_unknown_divination_native_log_recovers_after_reload_without_resending(env, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(message_log_recovery, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", message_log_recovery.find_recent_message_log_commands)
    monkeypatch.setattr(concubine, "find_message_log_replies", message_log_recovery.find_message_log_replies)
    env.send.return_value = None
    assert not asyncio.run(start(env))
    receipt(env, log=True)
    env.identity["pending_tasks"] = {}
    assert persistence.save_state() and persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    path = tmp_path / (datetime.fromtimestamp(NOW, concubine.TZ_LOCAL).strftime("%Y-%m-%d") + ".log")
    entry = {
        "ts": datetime.fromtimestamp(NOW + 1, concubine.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "event_type": "message", "sender_id": BOT, "sender_is_bot": True, "chat_id": CHAT,
        "message_id": ROOT + 1, "reply_to_msg_id": ROOT, "server_event_at": NOW + 1, "text": SUCCESS,
    }
    with path.open("a") as log:
        log.write(json.dumps(entry) + "\n")
    with state_module.use_identity(ID):
        assert asyncio.run(concubine.divination_actions.recover(NOW + 100))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_tianji_due_at"] == NOW + 1 + concubine.CONCUBINE_TIANJI_CD_SEC + concubine.CD_BUFFER_SEC
    env.send.assert_awaited_once()


@pytest.mark.parametrize("field,value", [("msg_id", True), ("account_id", "8801"), ("snapshot_at", False),
                                       ("affinity", False), ("reply_msg_id", True), ("reply_at", float("nan")),
                                       ("plan_key", "foreign"), ("extra", True)])
def test_corrupt_divination_record_cannot_reopen_completed_action(env, field, value):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    env.identity[FIELD][field] = value
    with state_module.use_identity(ID):
        assert concubine.divination_actions.record() is None
        assert concubine.divination_actions.block_reason() == "invalid"
    assert not asyncio.run(start(env))


@pytest.mark.parametrize("field,value", [("due_at", False), ("voyage_wait_until", False), ("cost", True),
                                       ("applied", 1), ("chain", None), ("text", []), ("outcome", "foreign")])
def test_corrupt_divination_result_is_not_a_completed_fact(env, field, value):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    env.identity[FIELD]["result"][field] = value
    with state_module.use_identity(ID):
        assert concubine.divination_actions.record() is None


def test_divination_log_recovery_preserves_foreign_pending_and_uses_server_clock(env, monkeypatch):
    assert asyncio.run(start(env))
    receipt(env)
    env.identity["pending_tasks"][(CHAT - 1, ROOT)] = {"cmd": COMMAND, "chat_id": CHAT - 1}
    entry = {"message_id": ROOT + 1, "chat_id": CHAT, "reply_to_msg_id": ROOT, "event_type": "message",
             "sender_id": BOT, "sender_is_bot": True, "server_event_at": NOW + 1, "text": SUCCESS}
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[entry]))
    with state_module.use_identity(ID):
        assert asyncio.run(concubine.divination_actions.recover(NOW + 10000))
    assert env.identity[FIELD]["reply_at"] == NOW + 1
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert (CHAT - 1, ROOT) in env.identity["pending_tasks"]
    env.send.assert_awaited_once()


def test_foreign_named_voyage_lock_cannot_set_current_partner_sailing(env):
    assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    text = "\u4f8d\u59be\u3010foreign\u3011\u6b63\u5728\u8fdc\u822a\u9014\u4e2d\uff0c\u6682\u65e0\u6cd5\u711a\u9999\u4ee3\u535c\u3002"
    assert not asyncio.run(reply(env, text))
    assert env.identity == before


def test_boolean_current_snapshot_does_not_authorize_divination_projection(env):
    env.identity["concubine_last_snapshot_at"] = 0
    assert asyncio.run(start(env))
    env.identity["concubine_last_snapshot_at"] = False
    assert asyncio.run(reply(env))
    assert env.identity[FIELD]["result"]["applied"] is False
    assert env.identity["concubine_tianji_due_at"] == NOW - 1


@pytest.mark.parametrize("retry", [None, NOW - 1, NOW])
def test_unsent_divination_without_valid_backoff_cannot_reopen(env, retry, monkeypatch):
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(*_args, **_kwargs):
        block.update(status="unsent", code="send_queue_timeout", at=NOW)
        return None

    env.send.side_effect = sent
    assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unsent"
    if retry is None:
        del env.identity[FIELD]["retry_at"]
    else:
        env.identity[FIELD]["retry_at"] = retry
    with state_module.use_identity(ID):
        assert concubine.divination_actions.record() is None
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("key", ["concubine_name", "concubine_tianji_chain"])
def test_divination_rejects_multiline_name_or_effect_before_send(env, key):
    env.identity[key] = "one\ntwo"
    assert not asyncio.run(start(env))
    env.send.assert_not_awaited()
