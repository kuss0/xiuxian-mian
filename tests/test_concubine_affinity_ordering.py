import asyncio
import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from model import persistence, state as state_module
from model.features import concubine, wanxin
from tests import test_concubine_observed_queries as observed
from tests.test_concubine_query_lifecycle import BOT, CHAT, ID, NOW, PANEL, ROOT
from tests.test_concubine_query_lifecycle import env as env
from tests.test_wanxin_reply_contract import COST, GAIN, HEADERS, SAMPLES
from tests.yinluo_native_support import native_reply


AT = NOW + 10
RESULTS = [
    ("moon_greet", HEADERS["moon_greet"] + "\n" + GAIN, 209),
    ("moon_seal", HEADERS["moon_seal"] + "\n" + COST, 176),
    ("moon_status", SAMPLES["wanxin_moon_panel"], 314),
]


@pytest.fixture(autouse=True)
def isolated_save(monkeypatch):
    monkeypatch.setattr(wanxin, "save_state", Mock(return_value=True))


async def snapshot(env, value, *, root=ROOT + 100, owned=False, command_at=AT, at=AT, route="direct"):
    if owned:
        env.clock[0] = command_at
        env.send.return_value = SimpleNamespace(id=root, chat_id=CHAT, sent_at=command_at, send_started_at=command_at)
        with state_module.use_identity(ID):
            assert await concubine._send_status_query("status", command_at)
    return await observed.deliver(
        env, route, text=PANEL.replace("184", str(value)), root=root,
        command_at=command_at, at=at, now=max(AT + 20, at),
    )


async def effect(action, text, *, root=ROOT, command_at=AT, at=AT):
    event = replace(native_reply(
        ID, wanxin.WANXIN_ACTION_COMMANDS[action], text, at,
        root=root, command_at=command_at, chat=CHAT,
    ), sender_id=BOT)
    with state_module.use_identity(ID):
        assert await wanxin.handle_wanxin_reply(text, AT + 20, event=event)
    return event


def evidence_point(*, msg_id=ROOT + 1, chat=CHAT, at=AT, edited=False):
    return {"at": at, "evidence": {"source": "telegram", "chat_id": chat, "msg_id": msg_id, "edited": edited}}


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("action,text,expected", RESULTS, ids=[case[0] for case in RESULTS])
def test_same_second_older_effect_is_already_in_a_later_native_snapshot(env, owned, action, text, expected):
    expected = 400 if action == "moon_status" else expected
    assert asyncio.run(snapshot(env, expected, owned=owned))
    before = copy.deepcopy(env.identity["concubine_status_query"])
    asyncio.run(effect(action, text))
    assert env.identity["concubine_affinity"] == expected
    assert env.identity["concubine_status_query"] == before
    assert env.identity["wanxin_observation"]["reply_points"][action]["handled"]


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("action,text,expected", RESULTS, ids=[case[0] for case in RESULTS])
def test_same_second_newer_effect_is_not_dropped_by_a_native_snapshot(env, owned, action, text, expected):
    assert asyncio.run(snapshot(env, 200, owned=owned))
    asyncio.run(effect(action, text, root=ROOT + 200))
    assert env.identity["concubine_affinity"] == expected


@pytest.mark.parametrize("route", observed.ROUTES)
@pytest.mark.parametrize("action,text,expected", RESULTS, ids=[case[0] for case in RESULTS])
def test_same_second_old_manual_snapshot_cannot_undo_a_newer_wanxin_result(env, route, action, text, expected):
    env.identity["concubine_affinity"] = 200
    asyncio.run(effect(action, text, root=ROOT + 200))
    assert env.identity["concubine_affinity"] == expected
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(snapshot(env, 200, route=route))
    assert env.identity == before


@pytest.mark.parametrize("route", observed.ROUTES)
@pytest.mark.parametrize("action,text,expected", RESULTS, ids=[case[0] for case in RESULTS])
def test_same_second_new_manual_snapshot_can_include_an_earlier_wanxin_result(env, route, action, text, expected):
    env.identity["concubine_affinity"] = 200
    asyncio.run(effect(action, text))
    assert asyncio.run(snapshot(env, expected, route=route))
    assert env.identity["concubine_affinity"] == expected
    assert env.identity["concubine_status_query"]["origin"] == "observed"


@pytest.mark.parametrize("owned", [False, True])
@pytest.mark.parametrize("route", observed.ROUTES)
@pytest.mark.parametrize("reload", [False, True])
def test_same_second_round_trip_retains_the_newer_operation_order(env, owned, route, reload):
    env.identity["concubine_affinity"] = 200
    if owned:
        env.clock[0] = AT
        env.send.return_value = SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=AT, send_started_at=AT)
        with state_module.use_identity(ID):
            assert asyncio.run(concubine._send_status_query("status", AT))
    first = asyncio.run(effect("moon_greet", RESULTS[0][1], root=ROOT + 200))
    assert env.identity["concubine_affinity"] == 209
    revision = replace(first, event_type="edit", text=first.text.replace("+9", "+0"))
    with state_module.use_identity(ID):
        assert asyncio.run(wanxin.handle_wanxin_reply(revision.text, AT + 1, event=revision))
    assert env.identity["concubine_affinity"] == 200
    if reload:
        assert persistence.save_state()
        state_module._meta_state["identity_states"] = {}
        assert persistence.load_state()
        env.identity = state_module.get_identity_state(ID)
    handled = asyncio.run(snapshot(env, 184, root=ROOT, route=route))
    assert handled is owned
    assert env.identity["concubine_affinity"] == 200
    if owned:
        assert env.identity["concubine_status_query"]["status"] == "complete"
        assert env.identity["concubine_phase"] == "idle"
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("point", [None, evidence_point(chat=CHAT - 1), evidence_point(edited=True),
                                    evidence_point(msg_id=ROOT + 300), evidence_point(at=AT + 1),
                                    evidence_point(msg_id=True), evidence_point(at=str(AT))])
def test_edited_receipt_command_bound_does_not_invent_observation_order(env, point):
    event = asyncio.run(effect("moon_greet", RESULTS[0][1], root=ROOT + 200))
    revision = replace(event, event_type="edit", text=event.text.replace("+9", "+0"))
    with state_module.use_identity(ID):
        assert asyncio.run(wanxin.handle_wanxin_reply(revision.text, AT + 1, event=revision))
    before = copy.deepcopy(env.identity)
    assert not wanxin.wanxin_affinity_snapshot_is_stale(
        env.identity["wanxin_observation"], AT, now=AT + 2, observation_point=point,
    )
    assert env.identity == before


@pytest.mark.parametrize("kind", ["missing_command", "edited_command", "root", "chat", "future", "unhandled"])
def test_same_second_command_bound_requires_a_valid_handled_receipt(env, kind):
    event = asyncio.run(effect("moon_greet", RESULTS[0][1], root=ROOT + 200))
    revision = replace(event, event_type="edit", text=event.text.replace("+9", "+0"))
    with state_module.use_identity(ID):
        assert asyncio.run(wanxin.handle_wanxin_reply(revision.text, AT + 1, event=revision))
    observation = copy.deepcopy(env.identity["wanxin_observation"])
    receipt = observation["reply_points"]["moon_greet"]
    if kind == "missing_command":
        receipt.pop("command_point")
    elif kind == "edited_command":
        receipt["command_point"]["evidence"]["edited"] = True
    elif kind == "root":
        receipt["root"] += 1
    elif kind == "chat":
        receipt["command_point"]["evidence"]["chat_id"] = CHAT - 1
    elif kind == "future":
        receipt["point"]["at"] = AT + 100
    else:
        observation["reply_points"]["moon_greet"] = {
            key: value for key, value in receipt.items()
            if key in {"point", "command_point", "root", "fingerprint", "handled"}
        }
        observation["reply_points"]["moon_greet"]["handled"] = False
    before = copy.deepcopy(observation)
    assert not wanxin.wanxin_affinity_snapshot_is_stale(observation, AT, now=AT + 2, observation_point=evidence_point())
    assert observation == before


@pytest.mark.parametrize("route", observed.ROUTES)
@pytest.mark.parametrize("failure", ["false", "exception"])
def test_covered_owned_query_completion_rolls_back_without_losing_its_receipt(env, route, failure):
    env.identity["concubine_affinity"] = 200
    env.clock[0] = AT
    env.send.return_value = SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=AT, send_started_at=AT)
    with state_module.use_identity(ID):
        assert asyncio.run(concubine._send_status_query("status", AT))
    query = env.identity["concubine_status_query"]
    original = {"cmd": query["command"], "family": "concubine_status", "op_id": query["op_id"],
                "source_module": "concubine_status", "account_id": query["account_id"],
                "chat_id": CHAT, "message_id": ROOT, "sent_at": AT, "send_started_at": AT}
    sibling = dict(original, op_id="newer_query", message_id=ROOT + 400)
    env.identity["pending_tasks"] = {(CHAT, ROOT): original, (CHAT, ROOT + 400): sibling}
    event = asyncio.run(effect("moon_greet", RESULTS[0][1], root=ROOT + 200))
    revision = replace(event, event_type="edit", text=event.text.replace("+9", "+0"))
    with state_module.use_identity(ID):
        assert asyncio.run(wanxin.handle_wanxin_reply(revision.text, AT + 1, event=revision))
    before = copy.deepcopy(env.identity)
    if failure == "exception":
        env.save.side_effect = OSError("completion failure")
        with pytest.raises(OSError, match="completion failure"):
            asyncio.run(snapshot(env, 184, root=ROOT, route=route))
    else:
        env.save.return_value = False
        assert not asyncio.run(snapshot(env, 184, root=ROOT, route=route))
    assert env.identity == before
    env.save.side_effect = None
    env.save.return_value = True
    assert asyncio.run(snapshot(env, 184, root=ROOT, route=route))
    assert env.identity["concubine_affinity"] == 200
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["pending_tasks"] == {(CHAT, ROOT + 400): sibling}
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("kind", ["account", "missing_query", "query_pending", "clock", "panel", "chat", "scalar_type"])
def test_same_clock_coverage_requires_the_exact_current_native_query(env, kind):
    assert asyncio.run(snapshot(env, 209))
    if kind == "account":
        state_module.set_identity_account(ID, ID + 1)
    elif kind == "missing_query":
        env.identity["concubine_status_query"] = {}
    elif kind == "query_pending":
        env.identity["concubine_status_query"]["status"] = "sent"
    elif kind == "clock":
        env.identity["concubine_last_snapshot_at"] = AT - 1
    elif kind == "panel":
        env.identity["concubine_last_panel_msg_id"] += 1
    elif kind == "chat":
        env.identity["concubine_last_panel_chat_id"] = CHAT - 1
    else:
        env.identity["concubine_last_panel_msg_id"] = str(ROOT + 101)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not concubine.same_clock_native_status_covers(evidence_point(), now=AT + 1)
    assert env.identity == before


@pytest.mark.parametrize("point", [None, evidence_point(chat=CHAT - 1), evidence_point(edited=True),
                                    evidence_point(msg_id=ROOT + 200), evidence_point(at=AT + 1),
                                    evidence_point(msg_id=True), evidence_point(at=str(AT))])
def test_scalar_cross_chat_or_edited_points_do_not_invent_coverage(env, point):
    assert asyncio.run(snapshot(env, 209))
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not concubine.same_clock_native_status_covers(point, now=AT + 2)
    assert env.identity == before


@pytest.mark.parametrize("action,text,expected", RESULTS, ids=[case[0] for case in RESULTS])
def test_same_second_completion_clears_only_its_root_and_survives_reload(env, monkeypatch, action, text, expected):
    expected = 400 if action == "moon_status" else expected
    assert asyncio.run(snapshot(env, expected))
    command = wanxin.WANXIN_ACTION_COMMANDS[action]
    original = {"cmd": command, "chat_id": CHAT, "account_id": state_module.get_identity_account(ID),
                "message_id": ROOT, "sent_at": AT}
    sibling = dict(original, message_id=ROOT + 200, sent_at=AT + 10)
    env.identity["pending_tasks"] = {(CHAT, ROOT): original, (CHAT, ROOT + 200): sibling}
    monkeypatch.setattr(wanxin, "save_state", persistence.save_state)
    event = asyncio.run(effect(action, text))
    assert env.identity["pending_tasks"] == {(CHAT, ROOT + 200): sibling}
    assert env.identity["concubine_affinity"] == expected
    points = copy.deepcopy(env.identity["wanxin_observation"]["reply_points"])
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    assert env.identity["wanxin_observation"]["reply_points"] == points
    with state_module.use_identity(ID):
        assert asyncio.run(wanxin.handle_wanxin_reply(event.text, AT + 21, event=event))
    assert env.identity["concubine_affinity"] == expected
    assert set(env.identity["pending_tasks"]) == {(CHAT, ROOT + 200)}


@pytest.mark.parametrize("mode", ["false", "exception", "sqlite"])
def test_same_second_completion_save_failure_remains_replayable(env, monkeypatch, mode):
    assert asyncio.run(snapshot(env, 209))
    env.identity["pending_tasks"] = {(CHAT, ROOT): {
        "cmd": wanxin.CMD_WANXIN_MOON_GREET, "chat_id": CHAT,
        "message_id": ROOT, "account_id": state_module.get_identity_account(ID), "sent_at": AT,
    }}
    event = replace(native_reply(ID, wanxin.CMD_WANXIN_MOON_GREET, RESULTS[0][1], AT,
                                root=ROOT, command_at=AT, chat=CHAT), sender_id=BOT)
    before = copy.deepcopy(env.identity)
    if mode == "sqlite":
        assert persistence.save_state()
        monkeypatch.setattr(wanxin, "save_state", persistence.save_state)
        conn = persistence.get_db_conn()
        conn.execute(f"CREATE TEMP TRIGGER fail_affinity_order BEFORE INSERT ON identity_runtime_state "
                     f"WHEN NEW.send_as_id = {ID} BEGIN SELECT RAISE(ABORT, 'order failure'); END")
    elif mode == "false":
        wanxin.save_state.return_value = False
    else:
        wanxin.save_state.side_effect = OSError("order failure")
    try:
        with state_module.use_identity(ID):
            if mode == "exception":
                with pytest.raises(OSError, match="order failure"):
                    asyncio.run(wanxin.handle_wanxin_reply(event.text, AT + 20, event=event))
            else:
                assert not asyncio.run(wanxin.handle_wanxin_reply(event.text, AT + 20, event=event))
        assert env.identity == before
    finally:
        if mode == "sqlite":
            conn.execute("DROP TRIGGER fail_affinity_order")
    monkeypatch.setattr(wanxin, "save_state", persistence.save_state)
    with state_module.use_identity(ID):
        assert asyncio.run(wanxin.handle_wanxin_reply(event.text, AT + 21, event=event))
    assert env.identity["concubine_affinity"] == 209
    assert not env.identity["pending_tasks"]
