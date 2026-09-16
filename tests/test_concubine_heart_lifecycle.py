import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, persistence, runtime, state as state_module
from model import message_log_recovery
from model.features import concubine, passive_inbox
from tests import test_concubine_fragment_actions as fragment_tests
from tests import test_concubine_external_events as external_tests
from tests import test_concubine_heart_contract as contract
from tests.test_concubine_fragment_actions import ACCOUNT, BOT, CHAT, ID, NAME, NOW, ROOT
from tests.test_concubine_status_contract import HEAD, FIELDS


base_env = fragment_tests.env
FIELD = "concubine_heart_session"
COMMAND = concubine.CMD_CONCUBINE_HEART
CHOICE = concubine.CMD_CONCUBINE_HEART_STEADY
PROMPT = ROOT + 1
ROUNDS = [contract.FIRST + "\n" + contract.OPTIONS,
          "\n".join([contract.ACK1, contract.SECOND, contract.CONTINUE]),
          "\n".join([contract.ACK2, contract.THIRD, contract.CONTINUE])]


def panel(wait="\u53ef\u7528"):
    fields = dict(FIELDS, affinity="\u60c5\u7f18\u503c: 300",
                  heart_due_at="\u5171\u5386\u5fc3\u52ab\u51b7\u5374: " + wait)
    return "\n".join([HEAD, *fields.values()])


@pytest.fixture
def env(base_env, monkeypatch):
    base_env.identity.update(concubine_enabled=False, concubine_tianji_enabled=False,
                             concubine_heart_enabled=True, concubine_voyage_enabled=False,
                             concubine_affinity=300, concubine_heart_due_at=NOW - 1)
    monkeypatch.setattr(concubine.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(concubine, "_fire_and_forget", lambda coroutine: coroutine.close())
    if hasattr(concubine, "heart_actions"):
        monkeypatch.setattr(concubine.heart_actions, "_INFLIGHT", {})
        monkeypatch.setattr(concubine.heart_actions, "_FOLLOWUPS", {})
    base_env.clock[0] = NOW - 10
    base_env.send.return_value = SimpleNamespace(id=ROOT - 100, chat_id=CHAT,
                                                sent_at=NOW - 10, send_started_at=NOW - 10)
    with state_module.use_identity(ID):
        assert asyncio.run(concubine._send_status_query("status", NOW - 10))
        assert asyncio.run(concubine.handle_concubine_status_reply(
            panel(), NOW - 9, SimpleNamespace(id=ROOT - 100, chat_id=CHAT,
                                              raw_text=concubine.CMD_CONCUBINE_STATUS),
            current_msg_id=ROOT - 99, current_chat_id=CHAT, observed_at=NOW - 9,
            reply_context={"sender_id": BOT}))
    base_env.identity["next_concubine_time"] = NOW
    base_env.clock[0] = NOW
    base_env.send.return_value = SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW + .5, send_started_at=NOW)
    base_env.send.reset_mock()
    base_env.save.reset_mock()
    return base_env


async def start(env):
    with state_module.use_identity(ID):
        return await concubine._send_heart_command(env.clock[0])


async def choose(env):
    with state_module.use_identity(ID):
        return await concubine._send_heart_choice(env.clock[0])


async def reply(env, text=None, *, route="direct", context=None, root=ROOT, msg_id=PROMPT,
                at=None, chat=CHAT, sender=BOT, event_type="message"):
    text = ROUNDS[0] if text is None else text
    at = env.clock[0] + 1 if at is None else at
    command = COMMAND if root == ROOT else CHOICE
    parent = SimpleNamespace(id=root, chat_id=chat, sender_id=ID, raw_text=command)
    event = SimpleNamespace(id=msg_id, chat_id=chat, sender_id=sender, server_event_at=at)
    if context is None:
        context = {"send_as_id": ID, "account_id": ACCOUNT, "chat_id": chat,
                   "family": "concubine_heart", "root_msg_id": root, "reply_to_msg_id": root,
                   "sender_id": sender, "event_type": event_type}
    now = max(env.clock[0], at) + 1
    if route == "native":
        return await app._handle_routed_reply_event(event, text, now, parent, context, event_kind=event_type)
    if route == "passive":
        return await passive_inbox.handle_passive_module_card(
            text, now=now, reply_context=context, event=event, event_type=event_type)
    with state_module.use_identity(ID):
        return await concubine.handle_concubine_heart_reply(
            text, now, parent, matched_family="concubine_heart", current_msg_id=msg_id,
            current_chat_id=chat, observed_at=at, reply_context=context)


def receipt(env, *, probe=False, log=False):
    value = env.identity[FIELD]
    item = value["probe"] if probe else value["steps"][-1]
    msg_id = ROOT + 10 * item["round"] if not probe else ROOT + 100
    with state_module.use_identity(ID):
        return runtime._finalize_game_command_sent(
            item["command"], msg_id=msg_id, sent_at=env.clock[0] + .5, send_started_at=env.clock[0],
            send_as_id=ID, game_group_id=CHAT, topic_id=0, track=True, max_retry=0,
            append_sent_log=log, send_intent={"op_id": item["op_id"], "source_module": "concubine_heart"})


def next_choice(env, round_no):
    env.clock[0] += 30
    env.send.return_value = SimpleNamespace(id=ROOT + 10 * round_no, chat_id=CHAT,
                                           sent_at=env.clock[0] + .5, send_started_at=env.clock[0])


def test_external_observation_allows_owned_heart_choices_but_not_a_new_launch(env):
    state_module.update_send_as_profile(ID, username="heart_owner")
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    with state_module.use_identity(ID):
        assert asyncio.run(concubine.handle_concubine_affinity_event(
            external_tests.MOON.replace("query_owner", "heart_owner"), NOW + 2,
            SimpleNamespace(id=ROOT + 2, chat_id=CHAT, sender_id=BOT, server_event_at=NOW + 2)))
    for number in range(1, 4):
        next_choice(env, number)
        assert asyncio.run(choose(env))
        text = ROUNDS[number] if number < 3 else contract.SUCCESS
        assert asyncio.run(reply(env, text, event_type="edit"))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity[external_tests.FIELD]["status"] == "pending"
    sends = env.send.await_count
    assert not asyncio.run(start(env))
    assert env.send.await_count == sends
    with state_module.use_identity(ID):
        assert not concubine._has_available_partner()


def test_queued_new_heart_launch_observes_external_change_and_releases_unsent_phase(env, monkeypatch):
    state_module.update_send_as_profile(ID, username="heart_owner")
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(_command, **kwargs):
        assert kwargs["operation_check"]()
        assert await concubine.handle_concubine_affinity_event(
            external_tests.MOON.replace("query_owner", "heart_owner"), NOW,
            SimpleNamespace(id=ROOT - 1, chat_id=CHAT, sender_id=BOT, server_event_at=NOW))
        assert not kwargs["operation_check"]()
        block.update(status="unsent", code="pre_send_guard", at=NOW)
        return None

    env.send.side_effect = sent
    assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unsent"
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity[external_tests.FIELD]["status"] == "pending"


def test_heart_saves_owned_intent_before_zero_retry_transport(env):
    async def sent(command, **kwargs):
        value = env.identity[FIELD]
        step = value["steps"][0]
        assert (value["identity_id"], value["account_id"], value["chat_id"]) == (ID, ACCOUNT, CHAT)
        assert step["status"] == "sending" and step["command"] == command == COMMAND
        assert kwargs["track"] is True and kwargs["max_retry"] == 0
        assert kwargs["op_id"] == step["op_id"] and kwargs["source_module"] == "concubine_heart"
        assert kwargs["reply_to"] == ROOT - 99 and kwargs["target_chat_id"] == CHAT
        assert kwargs["operation_check"]()
        env.save.assert_called()
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(start(env))
    assert env.identity[FIELD]["steps"][0]["status"] == "sent"


def test_heart_known_launch_does_not_fabricate_business_cooldown(env):
    before = env.identity["concubine_heart_due_at"]
    assert asyncio.run(start(env))
    assert env.identity["concubine_heart_due_at"] == before


@pytest.mark.parametrize("mode", ["none", "exception", "cancel"])
def test_unknown_heart_launch_survives_restart_without_long_cooldown(env, mode):
    env.send.return_value = None
    if mode == "none":
        assert not asyncio.run(start(env))
    else:
        env.send.side_effect = OSError("fixture") if mode == "exception" else asyncio.CancelledError()
        with pytest.raises(OSError if mode == "exception" else asyncio.CancelledError):
            asyncio.run(start(env))
    assert env.identity["concubine_heart_due_at"] == NOW - 1
    assert env.identity[FIELD]["steps"][0]["status"] == "unknown"
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 1000)
    assert env.identity == before
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("route", ["native", "passive"])
def test_unowned_scalar_heart_settlement_has_no_mutation_authority(env, route):
    env.identity.update(concubine_phase="heart_choice_reply_pending", concubine_heart_msg_id=ROOT,
                        concubine_heart_prompt_msg_id=PROMPT, concubine_heart_round=3)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, contract.SUCCESS, route=route))
    assert env.identity == before


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_three_rounds_can_edit_the_same_lower_id_prompt_and_settle_once(env, route):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, route=route))
    for number in range(1, 4):
        next_choice(env, number)
        assert asyncio.run(choose(env))
        text = ROUNDS[number] if number < 3 else contract.SUCCESS
        assert asyncio.run(reply(env, text, route=route, event_type="edit"))
    assert env.identity[FIELD]["status"] == "complete"
    assert len(env.identity[FIELD]["steps"]) == 4
    assert env.identity["concubine_affinity"] == 307
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["concubine_heart_due_at"] == env.clock[0] + 1 + concubine.CONCUBINE_HEART_CD_SEC + concubine.CD_BUFFER_SEC
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, contract.SUCCESS, route=route, event_type="edit"))
    assert env.identity == before


def test_heart_round_ack_save_failure_cannot_release_or_schedule_choice(env):
    assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    env.save.return_value = False
    assert not asyncio.run(reply(env))
    assert env.identity == before
    env.save.return_value = True
    assert asyncio.run(reply(env))


def test_round_receipt_and_cooldown_survive_sqlite_reload(env):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, contract.COOLDOWN))
    assert persistence.save_state() and persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    env.identity["concubine_heart_due_at"] = 0
    env.identity["next_concubine_time"] = NOW
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
@pytest.mark.parametrize("number", [0, 1, 3])
def test_early_heart_reply_survives_late_receipt_and_duplicate_registration(env, route, number):
    if number:
        assert asyncio.run(start(env))
        assert asyncio.run(reply(env))
        for index in range(1, number):
            next_choice(env, index)
            assert asyncio.run(choose(env))
            assert asyncio.run(reply(env, ROUNDS[index], event_type="edit"))
        next_choice(env, number)

    async def sent(*_args, **_kwargs):
        message = receipt(env)
        text = ROUNDS[number] if number < 3 else contract.SUCCESS
        assert await reply(env, text, route=route, event_type="edit" if number else "message")
        receipt(env)
        return message

    env.send.side_effect = sent
    assert asyncio.run(choose(env) if number else start(env))
    assert env.identity[FIELD]["steps"][-1]["status"] == "answered"
    assert (CHAT, ROOT + 10 * number) not in env.identity["pending_tasks"]
    assert env.identity[FIELD]["status"] == ("complete" if number == 3 else "active")


@pytest.mark.parametrize("mode", ["none", "exception", "cancel"])
def test_unknown_choice_has_no_automatic_mutation_retry(env, mode):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    next_choice(env, 1)
    env.send.return_value = None
    if mode == "none":
        assert not asyncio.run(choose(env))
    else:
        env.send.side_effect = OSError("fixture") if mode == "exception" else asyncio.CancelledError()
        with pytest.raises(OSError if mode == "exception" else asyncio.CancelledError):
            asyncio.run(choose(env))
    assert env.identity[FIELD]["steps"][-1]["status"] == "unknown"
    assert env.identity["concubine_heart_due_at"] == NOW - 1
    env.send.side_effect = None
    for offset in (31, 121, 600, 86400):
        env.clock[0] = NOW + 30 + offset
        with state_module.use_identity(ID):
            concubine.restore_concubine_runtime(env.clock[0])
            asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
    assert [call.args[0] for call in env.send.await_args_list].count(CHOICE) == 1
    assert [call.args[0] for call in env.send.await_args_list].count(COMMAND) == 1


@pytest.mark.parametrize("number", [0, 1])
@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_late_heart_transport_does_not_write_a_replacement_owner(env, number, change):
    if number:
        assert asyncio.run(start(env))
        assert asyncio.run(reply(env))
        next_choice(env, number)
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
    assert not asyncio.run(choose(env) if number else start(env))
    assert state_module._meta_state == after


@pytest.mark.parametrize("number", [0, 1])
@pytest.mark.parametrize("change", ["global", "identity", "module", "chat", "partner", "snapshot", "affinity",
                                    "timer", "phase", "query", "divination", "voyage", "gift", "fragment", "pending"])
def test_queued_heart_send_rechecks_owner_controls_plan_and_siblings(env, monkeypatch, number, change):
    if number:
        assert asyncio.run(start(env))
        assert asyncio.run(reply(env))
        next_choice(env, number)
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(_command, **kwargs):
        assert kwargs["operation_check"]()
        if change == "global":
            state_module.set_global_enabled(False)
        elif change == "identity":
            state_module.set_identity_enabled(ID, False)
        elif change == "chat":
            state_module.set_game_group_id(CHAT - 1)
        else:
            key, value = {
                "module": ("concubine_heart_enabled", False), "partner": ("concubine_name", "foreign"),
                "snapshot": ("concubine_last_snapshot_at", NOW + 2), "affinity": ("concubine_affinity", 7),
                "timer": ("next_concubine_time", NOW + 99999), "phase": ("concubine_phase", "voyage_pending"),
                "query": ("concubine_status_query", {"invalid": True}),
                "divination": ("concubine_tianji_action", {"invalid": True}),
                "voyage": ("concubine_voyage_actions", {"invalid": True}),
                "gift": ("concubine_gift_actions", {"invalid": True}),
                "fragment": ("concubine_fragment_actions", {"invalid": True}),
                "pending": ("pending_tasks", {(CHAT, ROOT + 1000): {"cmd": concubine.CMD_CONCUBINE_DREAM}}),
            }[change]
            env.identity[key] = value
        assert not kwargs["operation_check"]()
        block.update(status="unsent", code="pre_send_guard", at=env.clock[0])
        return None

    env.send.side_effect = sent
    assert not asyncio.run(choose(env) if number else start(env))
    assert env.identity[FIELD]["steps"][-1]["status"] == "unsent"


@pytest.mark.parametrize("number", [0, 1])
@pytest.mark.parametrize("fresh", [True, False])
def test_only_fresh_definitely_unsent_heart_evidence_permits_retry(env, monkeypatch, number, fresh):
    if number:
        assert asyncio.run(start(env))
        assert asyncio.run(reply(env))
        next_choice(env, number)
    old_at = env.clock[0]
    block = {"status": "unsent", "code": "pre_send_guard", "at": old_at - 10}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(*_args, **_kwargs):
        if fresh:
            block["at"] = old_at
        return None

    env.send.side_effect = sent
    assert not asyncio.run(choose(env) if number else start(env))
    value = env.identity[FIELD]
    op_id = value["steps"][-1]["op_id"]
    assert value["steps"][-1]["status"] == ("unsent" if fresh else "unknown")
    assert env.identity["concubine_heart_due_at"] == NOW - 1
    assert not asyncio.run(choose(env) if number else start(env))
    env.clock[0] = old_at + 700
    if fresh:
        assert not asyncio.run(choose(env) if number else start(env))
        # A launch also needs a fresh owned panel after this backoff.
        if number:
            assert env.identity[FIELD]["steps"][-1]["op_id"] != op_id
    else:
        assert not asyncio.run(choose(env) if number else start(env))
        assert env.identity[FIELD]["steps"][-1]["op_id"] == op_id


@pytest.mark.parametrize("field,bad", [("sender_id", BOT + 9), ("send_as_id", ID + 9), ("account_id", ACCOUNT + 9),
                                      ("chat_id", CHAT - 9), ("root_msg_id", ROOT + 9),
                                      ("reply_to_msg_id", ROOT + 9), ("op_id", "f" * 32),
                                      ("source_module", "foreign"), ("family", "concubine_dream"),
                                      ("reply_to_command", CHOICE), ("reply_to_command_edited", True)])
def test_heart_reply_rejects_conflicting_route_metadata(env, field, bad):
    assert asyncio.run(start(env))
    context = {"sender_id": BOT, "send_as_id": ID, "account_id": ACCOUNT, "chat_id": CHAT,
               "root_msg_id": ROOT, "reply_to_msg_id": ROOT, "family": "concubine_heart"}
    context[field] = bad
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, context=context))
    assert env.identity == before


@pytest.mark.parametrize("field,bad", [("current_msg_id", 0), ("current_msg_id", True), ("current_msg_id", "88002"),
                                      ("current_chat_id", CHAT - 1), ("observed_at", NOW - 10),
                                      ("observed_at", NOW + 99), ("observed_at", True),
                                      ("observed_at", float("nan")), ("observed_at", None)])
def test_heart_reply_requires_strict_message_and_event_clock(env, field, bad):
    assert asyncio.run(start(env))
    kwargs = {"current_msg_id": PROMPT, "current_chat_id": CHAT, "observed_at": NOW + 1,
              "reply_context": {"sender_id": BOT}}
    kwargs[field] = bad
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine.handle_concubine_heart_reply(
            ROUNDS[0], NOW + 2, SimpleNamespace(id=ROOT, chat_id=CHAT, raw_text=COMMAND), **kwargs))
    assert env.identity == before


def finish_until_last_choice(env):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    for number in range(1, 4):
        next_choice(env, number)
        assert asyncio.run(choose(env))
        if number < 3:
            assert asyncio.run(reply(env, ROUNDS[number], event_type="edit"))


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
@pytest.mark.parametrize("pause", ["global", "identity", "module"])
def test_paused_heart_terminal_facts_are_applied_without_new_actions(env, route, pause):
    finish_until_last_choice(env)
    if pause == "global":
        state_module.set_global_enabled(False)
    elif pause == "identity":
        state_module.set_identity_enabled(ID, False)
    else:
        env.identity["concubine_heart_enabled"] = False
    sends = env.send.await_count
    assert asyncio.run(reply(env, contract.SUCCESS, route=route, event_type="edit"))
    assert env.identity["concubine_affinity"] == 307
    assert env.identity[FIELD]["result"]["affinity_applied"]
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(env.clock[0] + 1))
    assert env.send.await_count == sends


@pytest.mark.parametrize("field,value", [("concubine_affinity", 350), ("concubine_affinity", True),
                                       ("concubine_name", "replacement"), ("concubine_last_snapshot_at", NOW + 1)])
def test_newer_partner_projection_is_not_overwritten_by_heart_delta(env, field, value):
    finish_until_last_choice(env)
    env.identity[field] = value
    before_affinity = env.identity["concubine_affinity"]
    assert asyncio.run(reply(env, contract.SUCCESS, event_type="edit"))
    assert env.identity["concubine_affinity"] == before_affinity
    assert env.identity[FIELD]["status"] == "complete"
    assert not env.identity[FIELD]["result"]["affinity_applied"]


@pytest.mark.parametrize("delta,expected", [(-7, 293), (0, 300), (7, 307), (-301, 300), (2 ** 63 - 1, 300)])
def test_owned_heart_affinity_delta_has_exact_numeric_bounds(env, delta, expected):
    finish_until_last_choice(env)
    assert asyncio.run(reply(env, contract.SUCCESS.replace("+7", str(delta)), event_type="edit"))
    assert env.identity["concubine_affinity"] == expected
    assert env.identity[FIELD]["result"]["affinity_applied"] is (delta in {-7, 0, 7})


@pytest.mark.parametrize("stage", ["intent", "receipt", "round", "terminal"])
@pytest.mark.parametrize("mode", ["false", "exception"])
def test_heart_projection_save_failure_is_atomic_and_replayable(env, stage, mode):
    if stage == "terminal":
        finish_until_last_choice(env)
    elif stage == "round":
        assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    failure = False if mode == "false" else OSError("fixture save")
    if stage == "receipt":
        env.save.side_effect = [True, failure]
    elif mode == "false":
        env.save.return_value = False
    else:
        env.save.side_effect = failure
    action = reply(env, contract.SUCCESS if stage == "terminal" else None, event_type="edit" if stage == "terminal" else "message") if stage in {"round", "terminal"} else start(env)
    if mode == "false":
        assert not asyncio.run(action)
    else:
        with pytest.raises(OSError):
            asyncio.run(action)
    if stage == "receipt":
        assert env.identity[FIELD]["steps"][0]["status"] == "sending"
        assert env.identity["concubine_heart_due_at"] == NOW - 1
    else:
        assert env.identity == before
    env.save.return_value, env.save.side_effect = True, None
    if stage in {"round", "terminal"}:
        assert asyncio.run(reply(env, contract.SUCCESS if stage == "terminal" else None, event_type="edit" if stage == "terminal" else "message"))
    if stage == "intent":
        env.send.assert_not_awaited()


@pytest.mark.parametrize("text", [ROUNDS[0], ROUNDS[2], contract.SUCCESS])
def test_old_or_skipped_heart_round_cannot_consume_the_current_choice(env, text):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    next_choice(env, 1)
    assert asyncio.run(choose(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, text, event_type="edit"))
    assert env.identity == before


def test_each_round_can_use_a_new_scoped_prompt(env):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    for number in range(1, 4):
        next_choice(env, number)
        assert asyncio.run(choose(env))
        text = ROUNDS[number] if number < 3 else contract.SUCCESS
        assert asyncio.run(reply(env, text, root=ROOT + 10 * number, msg_id=ROOT + 10 * number + 1))
    assert env.identity[FIELD]["status"] == "complete"


@pytest.mark.parametrize("route", ["native", "passive"])
def test_unthreaded_edit_requires_the_exact_owned_prompt_not_a_single_active_role(env, route):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    next_choice(env, 1)
    assert asyncio.run(choose(env))
    context = {"family": "concubine_heart", "sender_id": BOT, "event_type": "edit"}
    if route == "native":
        context["send_as_id"] = ID
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, ROUNDS[1], root=0, msg_id=PROMPT + 50, route=route, context=context, event_type="edit"))
    assert env.identity == before
    assert asyncio.run(reply(env, ROUNDS[1], root=0, route=route, context=context, event_type="edit"))


async def tick(env, *, allow_send=True):
    with state_module.use_identity(ID):
        return await concubine.heart_actions.recover(env.clock[0], allow_send=allow_send)


def start_probe(env):
    env.clock[0] += 2
    env.send.return_value = SimpleNamespace(id=ROOT + 100, chat_id=CHAT, sent_at=env.clock[0] + .5,
                                           send_started_at=env.clock[0])
    assert asyncio.run(tick(env))
    assert env.identity[FIELD]["probe"]["status"] == "sent"


async def probe_reply(env, text, *, route="direct", context=None):
    at = env.clock[0] + 1
    parent = SimpleNamespace(id=ROOT + 100, chat_id=CHAT, raw_text=concubine.CMD_CONCUBINE_STATUS)
    context = context if context is not None else {"sender_id": BOT, "family": "concubine_status", "send_as_id": ID,
                                                 "reply_to_msg_id": ROOT + 100, "root_msg_id": ROOT + 100}
    event = SimpleNamespace(id=ROOT + 101, chat_id=CHAT, sender_id=BOT, server_event_at=at)
    if route == "native":
        return await app._handle_routed_reply_event(event, text, at + 1, parent, context)
    if route == "passive":
        return await passive_inbox.handle_passive_module_card(text, now=at + 1, reply_context=context, event=event, event_type="message")
    with state_module.use_identity(ID):
        return await concubine.handle_concubine_status_reply(
            text, at + 1, parent, current_msg_id=ROOT + 101, current_chat_id=CHAT, observed_at=at, reply_context=context)


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
@pytest.mark.parametrize("wait", ["\u53ef\u7528", "2\u5c0f\u65f6"])
def test_anchor_loss_uses_owned_read_only_calibration_not_an_invented_cooldown(env, route, wait):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, concubine.heart_contract.ANCHOR + "\uff0c\u9700\u91cd\u65b0\u5f15\u52a8\u5929\u52ab\u3002"))
    assert env.identity["concubine_heart_due_at"] == NOW - 1
    assert env.identity[FIELD]["status"] == "reconcile"
    start_probe(env)
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_STATUS
    assert asyncio.run(probe_reply(env, panel(wait), route=route))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity[FIELD]["result"]["outcome"] == ("reconciled_ready" if wait == "\u53ef\u7528" else "reconciled_cooldown")
    assert env.identity["concubine_affinity"] == 300
    assert env.identity["concubine_phase"] == "idle"


def test_unknown_mutation_can_be_closed_by_a_proven_cooldown_not_a_ready_panel(env):
    env.send.return_value = None
    assert not asyncio.run(start(env))
    env.clock[0] += concubine.CONCUBINE_PHASE_TIMEOUT_SEC
    start_probe(env)
    assert asyncio.run(probe_reply(env, panel()))
    assert env.identity[FIELD]["status"] == "reconcile"
    assert env.identity[FIELD]["steps"][0]["status"] == "unknown"
    assert env.identity["concubine_heart_due_at"] == NOW - 1
    env.clock[0] += concubine.heart_actions.PROBE_GAP_SEC + 2
    start_probe(env)
    assert asyncio.run(probe_reply(env, panel("2\u5c0f\u65f6")))
    assert env.identity[FIELD]["result"]["outcome"] == "reconciled_cooldown"


def test_in_progress_ready_panels_do_not_reopen_the_spending_chain_and_probes_are_bounded(env):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, concubine.heart_contract.PROGRESS + "\u3002"))
    for _ in range(concubine.heart_actions.PROBE_LIMIT):
        env.clock[0] += concubine.heart_actions.PROBE_GAP_SEC + 2
        start_probe(env)
        assert asyncio.run(probe_reply(env, panel()))
    sends = env.send.await_count
    for _ in range(5):
        env.clock[0] += 86400
        asyncio.run(tick(env))
    assert env.send.await_count == sends == 4
    assert env.identity[FIELD]["probe_count"] == 3
    assert env.identity["concubine_heart_due_at"] == NOW - 1


@pytest.mark.parametrize("entry_change", [{}, {"sender_id": BOT + 1}, {"sender_is_bot": False},
                                        {"chat_id": CHAT - 1}, {"server_event_at": NOW - 1},
                                        {"server_event_at": True}, {"event_type": "sent"}])
def test_owned_heart_log_recovery_requires_server_provenance_and_accepts_prompt_edits(env, monkeypatch, entry_change):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    next_choice(env, 1)
    assert asyncio.run(choose(env))
    entry = {"event_type": "edit", "message_id": PROMPT, "chat_id": CHAT, "sender_id": BOT,
             "sender_is_bot": True, "reply_to_msg_id": ROOT, "text": ROUNDS[1], "server_event_at": env.clock[0] + 1}
    entry.update(entry_change)
    monkeypatch.setattr(concubine.heart_actions, "iter_message_log_entries_between", lambda *_args: [(entry, env.clock[0])])
    env.clock[0] += 3
    assert asyncio.run(tick(env, allow_send=False))
    assert env.identity[FIELD]["steps"][-1]["status"] == ("answered" if not entry_change else "sent")
    env.send.assert_awaited()
    assert env.send.await_count == 2


def test_same_clock_conflicting_log_edits_do_not_select_an_arbitrary_result(env, monkeypatch):
    finish_until_last_choice(env)
    entry = {"event_type": "edit", "message_id": PROMPT, "chat_id": CHAT, "sender_id": BOT,
             "sender_is_bot": True, "reply_to_msg_id": ROOT, "text": contract.SUCCESS, "server_event_at": env.clock[0] + 1}
    conflict = dict(entry, text=contract.SUCCESS.replace("+7", "+70"))
    monkeypatch.setattr(concubine.heart_actions, "iter_message_log_entries_between", lambda *_args: [(entry, env.clock[0]), (conflict, env.clock[0])])
    env.clock[0] += 3
    assert asyncio.run(tick(env, allow_send=False))
    assert env.identity[FIELD]["steps"][-1]["status"] == "sent"
    assert env.identity["concubine_affinity"] == 300


def test_sqlite_and_native_log_reload_recover_unknown_launch_before_transport_returns(env, monkeypatch, tmp_path):
    env.send.return_value = None
    assert not asyncio.run(start(env))
    value = env.identity[FIELD]
    op_id = value["steps"][0]["op_id"]
    stamp = lambda at: datetime.fromtimestamp(at, concubine.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8")
    entries = [
        {"ts": stamp(NOW), "event_type": "sent", "message_id": ROOT, "chat_id": CHAT,
         "sender_id": ID, "account_id": ACCOUNT, "source_module": "concubine_heart", "op_id": op_id, "text": COMMAND},
        {"ts": stamp(NOW + 1), "event_type": "message", "message_id": PROMPT, "chat_id": CHAT,
         "sender_id": BOT, "sender_is_bot": True, "reply_to_msg_id": ROOT, "server_event_at": NOW + 1, "text": ROUNDS[0]},
    ]
    log = tmp_path / (datetime.fromtimestamp(NOW, concubine.TZ_LOCAL).date().isoformat() + ".log")
    log.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")
    monkeypatch.setattr(message_log_recovery, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", message_log_recovery.find_recent_message_log_commands)
    assert persistence.save_state() and persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    env.clock[0] += 3
    assert asyncio.run(tick(env, allow_send=False))
    assert env.identity[FIELD]["steps"][0]["status"] == "answered"
    assert env.identity[FIELD]["steps"][0]["op_id"] == op_id
    assert env.identity["concubine_heart_due_at"] == NOW - 1
    env.send.assert_awaited_once()


@pytest.mark.parametrize("text", contract.BAD_TEXTS)
def test_malformed_heart_text_cannot_mutate_an_owned_terminal_wait(env, text):
    finish_until_last_choice(env)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, text, event_type="edit"))
    assert env.identity == before


@pytest.mark.parametrize("partner,accepted", [(NAME, True), ("foreign", False)])
def test_owned_heart_voyage_refusal_requires_the_original_partner(env, partner, accepted):
    assert asyncio.run(start(env))
    text = f"\u4f8d\u59be\u3010{partner}\u3011\u6b63\u5728\u8fdc\u822a\u9014\u4e2d\uff0c\u6682\u65e0\u6cd5\u5171\u5386\u5fc3\u52ab\u3002"
    before = copy.deepcopy(env.identity)
    assert asyncio.run(reply(env, text)) is accepted
    if not accepted:
        assert env.identity == before
    else:
        assert env.identity[FIELD]["result"]["outcome"] == "voyage_lock"
        assert env.identity["concubine_heart_due_at"] == NOW - 1


@pytest.mark.parametrize("text,outcome", [
    ("\u4fee\u4e3a\u4e0d\u8db3\uff0c\u5f00\u542f\u5171\u5386\u5fc3\u52ab\u9700\u8981 180 \u4fee\u4e3a\u3002", "resource_shortage"),
    ("\u8bf7\u56de\u590d\u4e00\u6761\u5305\u542b\u4f8d\u59be/\u9053\u4fa3\u5185\u5bb9\u7684\u6d88\u606f\uff0c\u518d\u4f7f\u7528 .\u5171\u5386\u5fc3\u52ab\u3002", "missing_panel"),
])
def test_explicit_heart_refusal_finishes_request_without_fabricating_cd_or_immediate_send(env, text, outcome):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, text))
    assert env.identity[FIELD]["result"]["outcome"] == outcome
    assert env.identity["concubine_heart_due_at"] == NOW - 1
    assert env.identity["concubine_phase"] == "idle"
    env.send.assert_awaited_once()
    if outcome == "resource_shortage":
        assert env.identity[FIELD]["next_at"] > NOW
        assert not asyncio.run(start(env))


def test_timed_in_progress_delays_read_only_calibration_without_guessing_business_cd(env):
    assert asyncio.run(start(env))
    text = concubine.heart_contract.PROGRESS + "\uff0c\u8bf7\u5728 2\u5206\u949f \u540e\u518d\u8bd5\u3002"
    assert asyncio.run(reply(env, text))
    assert env.identity[FIELD]["probe_after"] == NOW + 1 + 120 + concubine.CD_BUFFER_SEC
    assert env.identity["concubine_heart_due_at"] == NOW - 1
    env.clock[0] += 60
    asyncio.run(tick(env))
    env.send.assert_awaited_once()


def test_global_heart_spacing_uses_owned_launch_evidence_across_identities(env):
    other_id = ID + 1
    state_module.set_identity_account(other_id, ACCOUNT)
    other = state_module.get_identity_state(other_id)
    other.update({key: copy.deepcopy(value) for key, value in env.identity.items()
                  if key.startswith("concubine_") and key not in {"concubine_status_query", FIELD}})
    env.clock[0] = NOW - 5
    env.send.return_value = SimpleNamespace(id=ROOT - 50, chat_id=CHAT, sent_at=NOW - 5, send_started_at=NOW - 5)
    with state_module.use_identity(other_id):
        assert asyncio.run(concubine._send_status_query("status", NOW - 5))
        assert asyncio.run(concubine.handle_concubine_status_reply(
            panel(), NOW - 4, SimpleNamespace(id=ROOT - 50, chat_id=CHAT, raw_text=concubine.CMD_CONCUBINE_STATUS),
            current_msg_id=ROOT - 49, current_chat_id=CHAT, observed_at=NOW - 4, reply_context={"sender_id": BOT}))
    env.clock[0] = NOW
    env.send.return_value = SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW + .5, send_started_at=NOW)
    assert asyncio.run(start(env))
    with state_module.use_identity(other_id):
        assert not asyncio.run(concubine._send_heart_command(NOW + 1))
    assert other["next_concubine_time"] == NOW + .5 + concubine.CONCUBINE_HEART_GLOBAL_START_GAP_SEC
    env.clock[0] = NOW + 301
    env.send.return_value = SimpleNamespace(id=ROOT + 1000, chat_id=CHAT, sent_at=env.clock[0] + .5, send_started_at=env.clock[0])
    with state_module.use_identity(other_id):
        assert asyncio.run(concubine._send_heart_command(env.clock[0]))


def test_unowned_cached_or_logged_panel_is_not_used_as_the_heart_reply_target(env, monkeypatch):
    env.identity["concubine_status_query"] = {}
    monkeypatch.setattr(concubine, "_iter_message_log_entries_between", Mock(side_effect=AssertionError("unowned log lookup")))
    assert not asyncio.run(start(env))
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_STATUS
    assert "reply_to" not in env.send.await_args.kwargs
    assert not env.identity[FIELD]


@pytest.mark.parametrize("phase", ["heart_pending", "heart_choice_pending", "heart_choice_reply_pending"])
def test_orphan_heart_state_is_preserved_in_startup_and_phaseful_cleanup(env, phase):
    env.identity.update(concubine_phase=phase, concubine_heart_msg_id=ROOT,
                        concubine_heart_prompt_msg_id=PROMPT, concubine_heart_round=1)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 86400)
        asyncio.run(concubine.run_concubine_phaseful_cleanup_scheduler(NOW + 86400))
        asyncio.run(concubine.run_concubine_scheduler(NOW + 86400))
    assert env.identity == before
    env.send.assert_not_awaited()


@pytest.mark.parametrize("change", ["none", "global", "module", "account", "replace", "delete", "round", "timer"])
def test_delayed_choice_binds_owner_operation_and_controls_across_sleep(env, monkeypatch, change):
    queued = []
    monkeypatch.setattr(concubine, "_fire_and_forget", queued.append)
    monkeypatch.setattr(concubine.heart_actions.asyncio, "sleep", AsyncMock())
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    assert len(queued) == 1
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    assert env.identity == before
    assert len(queued) == 1
    next_choice(env, 1)
    if change == "global":
        state_module.set_global_enabled(False)
    elif change == "module":
        env.identity["concubine_heart_enabled"] = False
    elif change == "account":
        state_module.set_identity_account(ID, ACCOUNT + 1)
    elif change == "replace":
        state_module._meta_state["identity_states"][ID] = copy.deepcopy(env.identity)
    elif change == "delete":
        state_module.remove_identity(ID)
    elif change == "round":
        env.identity["concubine_heart_round"] = 3
    elif change == "timer":
        env.identity["next_concubine_time"] = NOW + 99999
    asyncio.run(queued[0])
    assert env.send.await_count == (2 if change == "none" else 1)


@pytest.mark.parametrize("same_guard", [True, False])
def test_heart_terminal_cleanup_only_closes_its_exact_guard_and_pending_root(env, monkeypatch, same_guard):
    from model import action_guard

    monkeypatch.setattr(action_guard, "_recent_closed_command_guards", {})
    finish_until_last_choice(env)
    receipt(env)
    guard = {"last_msg_id": ROOT if same_guard else ROOT + 99, "last_chat_id": CHAT,
             "command": COMMAND, "action_key": "concubine_heart", "first_sent_at": NOW,
             "last_sent_at": NOW, "attempt": 1}
    env.identity["action_guard_sessions"]["concubine_heart"] = guard
    sibling = {"cmd": COMMAND, "family": "concubine_heart", "source_module": "replacement", "op_id": "replacement"}
    env.identity["pending_tasks"][(CHAT, ROOT + 1000)] = sibling
    assert asyncio.run(reply(env, contract.SUCCESS, event_type="edit"))
    assert (CHAT, ROOT + 30) not in env.identity["pending_tasks"]
    assert env.identity["pending_tasks"][(CHAT, ROOT + 1000)] == sibling
    assert ("concubine_heart" in env.identity["action_guard_sessions"]) is not same_guard


@pytest.mark.parametrize("replacement", [0, True, ROOT + 99])
def test_completed_heart_cannot_clear_a_replacement_scalar_phase(env, replacement):
    finish_until_last_choice(env)
    env.identity["concubine_heart_msg_id"] = replacement
    env.identity["next_concubine_time"] = NOW + 99999
    before_phase = env.identity["concubine_phase"]
    assert asyncio.run(reply(env, contract.SUCCESS, event_type="edit"))
    assert env.identity["concubine_heart_msg_id"] == replacement
    assert type(env.identity["concubine_heart_msg_id"]) is type(replacement)
    assert env.identity["concubine_phase"] == before_phase
    assert env.identity["next_concubine_time"] == NOW + 99999


def test_recovered_heart_result_is_idempotent_for_repeated_log_edits(env, monkeypatch):
    finish_until_last_choice(env)
    at = env.clock[0] + 1
    entry = {"event_type": "edit", "message_id": PROMPT, "chat_id": CHAT, "sender_id": BOT,
             "sender_is_bot": True, "reply_to_msg_id": ROOT, "text": contract.SUCCESS, "server_event_at": at}
    monkeypatch.setattr(concubine.heart_actions, "iter_message_log_entries_between", lambda *_args: [(entry, at)])
    env.clock[0] += 2
    assert asyncio.run(tick(env, allow_send=False))
    assert env.identity["concubine_affinity"] == 307
    before = copy.deepcopy(env.identity)
    asyncio.run(tick(env, allow_send=False))
    assert env.identity == before


@pytest.mark.parametrize("path,bad", [
    (("projection", "concubine_phase"), []),
    (("projection", "concubine_phase"), {}),
    (("projection", "concubine_heart_round"), True),
    (("steps", 0, "status"), []),
    (("steps", 0, "msg_id"), True),
    (("steps", 0, "sent_at"), NOW - 10),
    (("steps", 0, "dispatch_at"), "1700000500"),
    (("steps", 0, "op_id"), "bad"),
    (("status",), []),
    (("probe_count",), True),
    (("probe",), []),
    (("result",), {"outcome": "settlement"}),
])
def test_malformed_heart_record_fails_closed_without_raising_or_resetting(env, path, bad):
    assert asyncio.run(start(env))
    target = env.identity[FIELD]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert concubine.heart_actions.record() is None
        assert concubine.heart_actions.block_reason() == "invalid"
        assert asyncio.run(tick(env))
        assert not asyncio.run(start(env))
    assert env.identity == before
    env.send.assert_awaited_once()


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_ambiguous_owned_heart_receipts_cannot_bind_a_reply(env, route):
    env.send.return_value = None
    assert not asyncio.run(start(env))
    receipt(env)
    second = copy.deepcopy(env.identity["pending_tasks"][(CHAT, ROOT)])
    second["message_id"] = ROOT + 1000
    env.identity["pending_tasks"][(CHAT, ROOT + 1000)] = second
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, route=route))
    assert env.identity == before
    assert env.identity[FIELD]["steps"][0]["msg_id"] == 0


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_early_probe_response_is_committed_before_late_transport_receipt(env, route):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, concubine.heart_contract.PROGRESS + "\u3002"))
    env.clock[0] += 4

    async def sent(command, **kwargs):
        assert command == concubine.CMD_CONCUBINE_STATUS and kwargs["operation_check"]()
        message = receipt(env, probe=True)
        assert await probe_reply(env, panel("2\u5c0f\u65f6"), route=route)
        receipt(env, probe=True)
        return message

    env.send.side_effect = sent
    assert asyncio.run(tick(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity[FIELD]["probe"]["status"] == "answered"
    assert env.identity[FIELD]["result"]["outcome"] == "reconciled_cooldown"
    assert (CHAT, ROOT + 100) not in env.identity["pending_tasks"]


@pytest.mark.parametrize("stage", ["intent", "receipt", "reply", "expiry"])
@pytest.mark.parametrize("mode", ["false", "exception"])
def test_heart_probe_save_failures_retain_exact_owned_work(env, stage, mode):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, concubine.heart_contract.PROGRESS + "\u3002"))
    if stage in {"reply", "expiry"}:
        start_probe(env)
        receipt(env, probe=True)
    env.clock[0] += concubine.CONCUBINE_PHASE_TIMEOUT_SEC + 2 if stage == "expiry" else 4
    env.identity[FIELD]["replay_after"] = env.clock[0] + 60
    env.send.return_value = SimpleNamespace(id=ROOT + 100, chat_id=CHAT,
                                           sent_at=env.clock[0] + .5, send_started_at=env.clock[0])
    before = copy.deepcopy(env.identity)
    sends = env.send.await_count
    failure = False if mode == "false" else OSError("probe save fixture")
    if stage == "receipt":
        env.save.side_effect = [True, failure]
    elif mode == "false":
        env.save.return_value = False
    else:
        env.save.side_effect = failure
    action = probe_reply(env, panel("2\u5c0f\u65f6")) if stage == "reply" else tick(env)
    if mode == "exception":
        with pytest.raises(OSError):
            asyncio.run(action)
    else:
        asyncio.run(action)
    if stage == "receipt":
        assert env.identity[FIELD]["probe"]["status"] == "sending"
        assert env.identity[FIELD]["probe_count"] == 1
    else:
        assert env.identity == before
    assert env.send.await_count == sends + (stage == "receipt")
    assert env.identity["concubine_heart_due_at"] == NOW - 1
    env.save.return_value, env.save.side_effect = True, None
    if stage == "reply":
        assert asyncio.run(probe_reply(env, panel("2\u5c0f\u65f6")))
    elif stage == "expiry":
        assert asyncio.run(tick(env, allow_send=False))
        assert env.identity[FIELD]["probe"]["status"] == "expired"
        assert (CHAT, ROOT + 100) not in env.identity["pending_tasks"]


@pytest.mark.parametrize("finish_probe", ["reply", "expiry"])
def test_late_round_waits_for_existing_probe_then_continues_same_session(env, finish_probe):
    assert asyncio.run(start(env))
    env.clock[0] += concubine.CONCUBINE_PHASE_TIMEOUT_SEC
    start_probe(env)
    assert asyncio.run(reply(env))
    next_choice(env, 1)
    sends = env.send.await_count
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(choose(env))
    assert env.identity == before
    assert env.send.await_count == sends
    if finish_probe == "reply":
        assert asyncio.run(probe_reply(env, panel()))
    else:
        env.clock[0] += concubine.CONCUBINE_PHASE_TIMEOUT_SEC
        assert asyncio.run(tick(env, allow_send=False))
    env.clock[0] += 2
    env.send.return_value = SimpleNamespace(id=ROOT + 200, chat_id=CHAT,
                                           sent_at=env.clock[0] + .5, send_started_at=env.clock[0])
    assert asyncio.run(choose(env))
    with state_module.use_identity(ID):
        assert concubine.heart_actions.record() is not None
    assert env.identity[FIELD]["steps"][-1]["round"] == 1
    assert env.send.await_args.kwargs["target_chat_id"] == CHAT
    assert env.send.await_args.kwargs["reply_to"] == PROMPT


@pytest.mark.parametrize("route", ["direct", "native", "passive"])
def test_original_chat_heart_result_is_accepted_after_default_group_changes(env, route):
    finish_until_last_choice(env)
    state_module.set_game_group_id(CHAT - 1)
    next_at = env.identity["next_concubine_time"]
    assert asyncio.run(reply(env, contract.SUCCESS, route=route, event_type="edit"))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_affinity"] == 307
    assert env.identity["next_concubine_time"] == next_at
    assert env.send.await_count == 4


def prepare_outer_scheduler(monkeypatch):
    monkeypatch.setattr(app, "get_identity_ids", lambda: [ID])
    monkeypatch.setattr(app, "_is_identity_account_offline", lambda _identity_id: False)
    monkeypatch.setattr(app, "is_identity_weak", lambda *_args: False)
    monkeypatch.setattr(app, "has_phaseful_summary_block", lambda *_args: False)
    monkeypatch.setattr(app, "enforce_identity_module_availability", Mock())
    monkeypatch.setattr(app, "console_log", Mock())
    monkeypatch.setattr(app, "_PHASEFUL_IDENTITY_SCHEDULERS", ())
    monkeypatch.setattr(app, "_ORDINARY_IDENTITY_SCHEDULERS", (concubine.run_concubine_scheduler,))


def test_outer_scheduler_resumes_paused_round_before_scalar_timeout(env, monkeypatch):
    prepare_outer_scheduler(monkeypatch)
    assert asyncio.run(start(env))
    state_module.set_global_enabled(False)
    assert asyncio.run(reply(env))
    scalar_due = env.identity["next_concubine_time"]
    next_choice(env, 1)
    assert scalar_due > env.clock[0]
    state_module.set_global_enabled(True)
    asyncio.run(app._run_identity_schedulers(env.clock[0]))
    assert env.send.await_count == 2
    assert env.identity[FIELD]["steps"][-1]["status"] == "sent"


def test_outer_scan_failure_preserves_owned_heart_plan_and_can_resume(env, monkeypatch):
    prepare_outer_scheduler(monkeypatch)
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env))
    next_choice(env, 1)
    before = copy.deepcopy(env.identity)
    failed = Mock(side_effect=OSError("heart replay fixture"))
    monkeypatch.setattr(concubine.heart_actions, "iter_message_log_entries_between", failed)
    asyncio.run(app._run_due_concubine_schedulers(env.clock[0]))
    assert env.identity["next_concubine_time"] == before["next_concubine_time"]
    with state_module.use_identity(ID):
        assert concubine._status_query_plan(concubine._status_query_owner()) == env.identity[FIELD]["plan_key"]
    monkeypatch.setattr(concubine.heart_actions, "iter_message_log_entries_between", lambda *_args: [])
    asyncio.run(app._run_due_concubine_schedulers(env.clock[0]))
    assert env.send.await_count == 2
    assert env.identity[FIELD]["steps"][-1]["status"] == "sent"


@pytest.mark.parametrize("phase", ["status_pending", "heart_pending", "heart_choice_pending", "heart_choice_reply_pending"])
def test_legacy_timeout_does_not_invent_a_heart_business_cooldown(env, phase):
    with state_module.use_identity(ID):
        concubine._backoff_after_pending_timeout(NOW, phase)
    assert env.identity["concubine_heart_due_at"] == NOW - 1


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_guard_save_failure_keeps_committed_result_and_retries_only_cleanup(env, monkeypatch, mode):
    from model import action_guard

    monkeypatch.setattr(action_guard, "_recent_closed_command_guards", {})
    finish_until_last_choice(env)
    guard = {"last_msg_id": ROOT, "last_chat_id": CHAT, "command": COMMAND,
             "action_key": "concubine_heart", "first_sent_at": NOW, "last_sent_at": NOW, "attempt": 1}
    env.identity["action_guard_sessions"]["concubine_heart"] = guard
    env.save.side_effect = [True, False if mode == "false" else OSError("guard save fixture")]
    assert asyncio.run(reply(env, contract.SUCCESS, event_type="edit"))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_affinity"] == 307
    assert env.identity["action_guard_sessions"]["concubine_heart"] == guard
    env.save.side_effect = None
    assert not asyncio.run(tick(env, allow_send=False))
    assert "concubine_heart" not in env.identity["action_guard_sessions"]
    assert env.identity["concubine_affinity"] == 307
    assert env.send.await_count == 4


@pytest.mark.parametrize("key,bad", [
    ("account_id", ACCOUNT + 1), ("op_id", "f" * 32), ("source_module", "foreign"),
    ("cmd", CHOICE), ("sent_at", True), ("send_started_at", NOW - 10),
    ("chat_id", CHAT - 1), ("message_id", ROOT + 1000),
])
def test_conflicting_heart_receipt_does_not_authorize_a_result(env, key, bad):
    env.send.return_value = None
    assert not asyncio.run(start(env))
    receipt(env)
    env.identity["pending_tasks"][(CHAT, ROOT)][key] = bad
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    assert env.identity == before


def test_passive_heart_reply_rejects_multiple_owned_identity_candidates(env):
    assert asyncio.run(start(env))
    other_id = ID + 1
    state_module.set_identity_account(other_id, ACCOUNT)
    other = state_module.get_identity_state(other_id)
    other.update(copy.deepcopy(env.identity))
    other[FIELD]["identity_id"] = other_id
    before = copy.deepcopy(state_module._meta_state)
    context = {"family": "concubine_heart", "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    assert not asyncio.run(reply(env, route="passive", context=context))
    assert state_module._meta_state == before


@pytest.mark.parametrize("route", ["native", "passive"])
@pytest.mark.parametrize("field", ["id", "chat_id", "sender_id"])
def test_heart_native_event_ids_are_not_coerced_from_strings(env, route, field):
    assert asyncio.run(start(env))
    fields = {"id": PROMPT, "chat_id": CHAT, "sender_id": BOT, "server_event_at": NOW + 1}
    fields[field] = str(fields[field])
    event = SimpleNamespace(**fields)
    context = {"family": "concubine_heart", "send_as_id": ID,
               "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    before = copy.deepcopy(env.identity)
    if route == "native":
        action = app._handle_routed_reply_event(
            event, ROUNDS[0], NOW + 2, SimpleNamespace(id=ROOT, chat_id=CHAT), context)
    else:
        action = passive_inbox.handle_passive_module_card(
            ROUNDS[0], now=NOW + 2, reply_context=context, event=event, event_type="message")
    assert not asyncio.run(action)
    assert env.identity == before


@pytest.mark.parametrize("status", ["active", "reconcile"])
def test_persisted_terminal_receipt_without_completion_cannot_reopen_heart(env, status):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, contract.COOLDOWN))
    env.identity[FIELD].update(status=status, result={})
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert concubine.heart_actions.record() is None
    assert asyncio.run(tick(env))
    assert env.identity == before
    env.send.assert_awaited_once()
