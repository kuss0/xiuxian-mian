import asyncio
import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from model import persistence, state as state_module
from model.features import cave_treasure_runtime as cave
from model.features import concubine, passive_inbox, wanxin
from tests import test_cave_read_only_lifecycle as cave_tests
from tests import test_concubine_query_lifecycle as query_tests
from tests.test_wanxin_reply_contract import COST, GAIN, HEADERS, SAMPLES
from tests.yinluo_native_support import native_reply


NOW = query_tests.NOW
EFFECTS = [("moon_greet", 209), ("moon_seal", 176)]
cave_env = cave_tests.env
query_env = query_tests.env


@pytest.fixture(autouse=True)
def isolated_wanxin_save(monkeypatch):
    monkeypatch.setattr(wanxin, "save_state", Mock(return_value=True))


def prepare_partner(identity):
    identity.update(
        concubine_name="\u5357\u5bab\u5a49", concubine_kind="\u9053\u5fc3\u4f8d\u59be",
        concubine_location="\u968f\u884c\u4e2d", concubine_availability="available",
        concubine_affinity=200, concubine_last_snapshot_at=NOW - 7200,
    )


async def apply_wanxin(identity_id, action, *, at=NOW + 3):
    body = {
        "moon_greet": HEADERS["moon_greet"] + "\n" + GAIN,
        "moon_seal": HEADERS["moon_seal"] + "\n" + COST,
        "moon_status": SAMPLES["wanxin_moon_panel"],
        "visit": SAMPLES["wanxin_visit"],
    }[action]
    event = replace(native_reply(
        identity_id, wanxin.WANXIN_ACTION_COMMANDS[action], body, at,
        root=query_tests.ROOT + 10, command_at=at - 1, chat=query_tests.CHAT,
    ), sender_id=query_tests.BOT)
    with state_module.use_identity(identity_id):
        assert await wanxin.handle_wanxin_reply(body, at + 1, event=event)
    return event


@pytest.mark.parametrize("kind", query_tests.KINDS)
@pytest.mark.parametrize("action,expected", EFFECTS)
def test_owned_old_read_completes_without_replacing_later_wanxin_affinity(query_env, kind, action, expected):
    env = query_env
    prepare_partner(env.identity)
    assert asyncio.run(query_tests.send_query(kind))
    query_tests.receipt(env)
    sibling = {"cmd": ".fixture", "chat_id": query_tests.CHAT, "sent_at": NOW}
    sibling_key = (query_tests.CHAT, query_tests.ROOT + 100)
    env.identity["pending_tasks"][sibling_key] = sibling
    asyncio.run(apply_wanxin(query_tests.ID, action))
    env.clock[0] = NOW + 10
    wanxin_before = copy.deepcopy(env.identity["wanxin_observation"])

    assert asyncio.run(query_tests.reply(env, text=cave_tests.CONCUBINE))
    assert env.identity["concubine_affinity"] == expected
    assert env.identity["concubine_last_snapshot_at"] == NOW - 7200
    assert env.identity["wanxin_observation"] == wanxin_before
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["pending_tasks"] == {sibling_key: sibling}
    env.gift.assert_not_awaited()


@pytest.mark.parametrize("action,expected", EFFECTS)
def test_saved_old_query_keeps_wanxin_affinity_after_reload(query_env, action, expected):
    env = query_env
    prepare_partner(env.identity)
    assert asyncio.run(query_tests.send_query("status"))
    query_tests.receipt(env)
    asyncio.run(apply_wanxin(query_tests.ID, action))
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(query_tests.ID)
    env.clock[0] = NOW + 10

    assert asyncio.run(query_tests.reply(env, text=cave_tests.CONCUBINE))
    assert env.identity["concubine_affinity"] == expected
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert not env.identity["pending_tasks"]
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(query_tests.reply(env, text=cave_tests.CONCUBINE))
    assert env.identity == before


@pytest.mark.parametrize("action,expected", EFFECTS)
def test_new_owned_read_after_wanxin_result_can_calibrate(query_env, action, expected):
    env = query_env
    prepare_partner(env.identity)
    asyncio.run(apply_wanxin(query_tests.ID, action, at=NOW - 1))
    assert env.identity["concubine_affinity"] == expected
    assert asyncio.run(query_tests.send_query("status"))
    query_tests.receipt(env)
    panel = cave_tests.CONCUBINE.replace("184", str(expected))
    assert asyncio.run(query_tests.reply(env, text=panel))
    assert env.identity["concubine_affinity"] == expected
    assert env.identity["concubine_last_snapshot_at"] == NOW + 1


def test_unrelated_wanxin_result_does_not_cancel_owned_status_read(query_env):
    env = query_env
    prepare_partner(env.identity)
    assert asyncio.run(query_tests.send_query("status"))
    query_tests.receipt(env)
    asyncio.run(apply_wanxin(query_tests.ID, "visit"))
    env.clock[0] = NOW + 10
    assert asyncio.run(query_tests.reply(env, text=cave_tests.CONCUBINE))
    assert env.identity["concubine_affinity"] == 184
    assert env.identity["concubine_last_snapshot_at"] == NOW + 1


@pytest.mark.parametrize("stage", ["session", "result"])
@pytest.mark.parametrize("action,expected", EFFECTS)
def test_inflight_miniapp_read_cannot_replace_wanxin_affinity(cave_env, stage, action, expected):
    env = cave_env
    prepare_partner(env.identity)
    state_module.set_game_group_id(query_tests.CHAT)
    state_module.set_game_bot_ids([query_tests.BOT])
    env.flow.return_value["data"] = cave_tests.payload(cave_tests.CONCUBINE)
    selected = env.session if stage == "session" else env.flow

    async def result(*_args, **kwargs):
        assert kwargs["operation_check"]()
        await apply_wanxin(cave_tests.ID, action)
        assert not kwargs["operation_check"]()
        return selected.return_value

    selected.side_effect = result
    response = asyncio.run(cave_tests.read("concubine"))
    assert not response["ok"]
    assert response["extra"]["status"] == "cancelled"
    assert env.identity["concubine_affinity"] == expected
    assert env.identity["concubine_last_snapshot_at"] == NOW - 7200
    assert env.flow.await_count == (stage == "result")
    concubine.send_game_command.assert_not_awaited()

    selected.side_effect = None
    env.flow.return_value["data"] = cave_tests.payload(cave_tests.CONCUBINE.replace("184", str(expected)))
    response = asyncio.run(cave.run_cave_public_tianjige_read_only(
        cave_tests.ID, cave_tests.ENTRY, cave_tests.COMMANDS["concubine"], now=NOW + 10,
    ))
    assert response["ok"]
    assert env.identity["concubine_affinity"] == expected
    assert env.identity["concubine_last_snapshot_at"] == NOW + 10


def test_other_identity_wanxin_result_does_not_cancel_miniapp_read(cave_env):
    env = cave_env
    prepare_partner(env.identity)
    state_module.set_game_group_id(query_tests.CHAT)
    state_module.set_game_bot_ids([query_tests.BOT])
    state_module.set_identity_account(query_tests.ID, query_tests.ACCOUNT)
    other = state_module.get_identity_state(query_tests.ID)
    prepare_partner(other)
    env.flow.return_value["data"] = cave_tests.payload(cave_tests.CONCUBINE)

    async def result(*_args, **kwargs):
        assert kwargs["operation_check"]()
        await apply_wanxin(query_tests.ID, "moon_greet")
        assert kwargs["operation_check"]()
        return env.flow.return_value

    env.flow.side_effect = result
    assert asyncio.run(cave_tests.read("concubine"))["ok"]
    assert env.identity["concubine_affinity"] == 184
    assert other["concubine_affinity"] == 209


async def manual_panel(env, route, *, at=NOW + 1, root=query_tests.ROOT):
    context = {
        "send_as_id": query_tests.ID, "account_id": query_tests.ACCOUNT,
        "family": "concubine_status", "chat_id": query_tests.CHAT,
        "root_msg_id": root, "reply_to_msg_id": root,
        "reply_to_command": concubine.CMD_CONCUBINE_STATUS,
        "reply_to_sender_id": query_tests.ID, "reply_to_server_at": at - 1,
        "reply_to_command_edited": False, "sender_id": query_tests.BOT,
    }
    with state_module.use_identity(query_tests.ID):
        if route == "native":
            return await concubine.handle_concubine_status_reply(
                cave_tests.CONCUBINE, env.clock[0], SimpleNamespace(
                    id=root, chat_id=query_tests.CHAT,
                    sender_id=query_tests.ID, raw_text=concubine.CMD_CONCUBINE_STATUS,
                ), matched_family="concubine_status", current_msg_id=root + 1,
                current_chat_id=query_tests.CHAT, observed_at=at, reply_context=context,
            )
        return await passive_inbox.handle_passive_module_card(
            cave_tests.CONCUBINE, now=env.clock[0], reply_context=context,
            event=SimpleNamespace(id=root + 1, chat_id=query_tests.CHAT,
                                  sender_id=query_tests.BOT, server_event_at=at),
            event_type="message",
        )


@pytest.mark.parametrize("route", ["native", "passive"])
@pytest.mark.parametrize("action,expected", EFFECTS + [("moon_status", 314)])
def test_old_unowned_panel_cannot_undo_a_newer_wanxin_affinity_result(query_env, route, action, expected):
    env = query_env
    prepare_partner(env.identity)
    asyncio.run(apply_wanxin(query_tests.ID, action))
    env.clock[0] = NOW + 10
    before = copy.deepcopy(env.identity)
    handled = asyncio.run(manual_panel(env, route))
    assert env.identity["concubine_affinity"] == expected
    assert not handled
    assert env.identity == before


def test_owned_read_stays_stale_when_wanxin_correction_restores_original_affinity(query_env):
    env = query_env
    prepare_partner(env.identity)
    assert asyncio.run(query_tests.send_query("status"))
    query_tests.receipt(env)
    event = asyncio.run(apply_wanxin(query_tests.ID, "moon_greet"))
    edited = replace(event, text=event.text.replace("+9", "+0"),
                     event_type="edit", server_event_at=NOW + 4)
    with state_module.use_identity(query_tests.ID):
        assert asyncio.run(wanxin.handle_wanxin_reply(edited.text, NOW + 5, event=edited))
        assert env.identity["concubine_affinity"] == 200
        assert concubine._status_query_plan(concubine._status_query_owner()) == env.identity["concubine_status_query"]["plan_key"]
    env.clock[0] = NOW + 10

    assert asyncio.run(query_tests.reply(env, text=cave_tests.CONCUBINE))
    assert env.identity["concubine_affinity"] == 200
    assert env.identity["concubine_last_snapshot_at"] == NOW - 7200
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert not env.identity["pending_tasks"]


@pytest.mark.parametrize("route", ["native", "passive"])
@pytest.mark.parametrize("action,expected", EFFECTS + [("moon_status", 314)])
def test_new_unowned_panel_can_calibrate_after_wanxin_result(query_env, route, action, expected):
    env = query_env
    prepare_partner(env.identity)
    asyncio.run(apply_wanxin(query_tests.ID, action))
    assert env.identity["concubine_affinity"] == expected
    env.clock[0] = NOW + 20
    assert asyncio.run(manual_panel(env, route, at=NOW + 10, root=query_tests.ROOT + 100))
    assert env.identity["concubine_affinity"] == 184
    assert env.identity["concubine_last_snapshot_at"] == NOW + 10


@pytest.mark.parametrize("route", ["native", "passive"])
def test_unrelated_wanxin_result_does_not_block_an_unowned_panel(query_env, route):
    env = query_env
    prepare_partner(env.identity)
    asyncio.run(apply_wanxin(query_tests.ID, "visit"))
    env.clock[0] = NOW + 10
    assert asyncio.run(manual_panel(env, route))
    assert env.identity["concubine_affinity"] == 184


@pytest.mark.parametrize("route", ["native", "passive"])
def test_old_unowned_panel_stays_rejected_after_wanxin_receipt_reload(query_env, route):
    env = query_env
    prepare_partner(env.identity)
    asyncio.run(apply_wanxin(query_tests.ID, "moon_seal"))
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(query_tests.ID)
    env.clock[0] = NOW + 10
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(manual_panel(env, route))
    assert env.identity == before


@pytest.mark.parametrize("route", ["native", "passive"])
def test_edited_moon_read_cannot_borrow_its_edit_clock(query_env, route):
    env = query_env
    prepare_partner(env.identity)
    event = asyncio.run(apply_wanxin(query_tests.ID, "moon_status"))
    edited = replace(event, text=event.text.replace("314", "315"),
                     event_type="edit", server_event_at=NOW + 30)
    with state_module.use_identity(query_tests.ID):
        assert asyncio.run(wanxin.handle_wanxin_reply(edited.text, NOW + 31, event=edited))
    assert env.identity["concubine_affinity"] == 315
    env.clock[0] = NOW + 40
    assert asyncio.run(manual_panel(env, route, at=NOW + 20, root=query_tests.ROOT + 100))
    assert env.identity["concubine_affinity"] == 184


@pytest.mark.parametrize("route", ["native", "passive"])
def test_gain_correction_retains_its_revision_clock_until_new_panel(query_env, route):
    env = query_env
    prepare_partner(env.identity)
    event = asyncio.run(apply_wanxin(query_tests.ID, "moon_greet"))
    edited = replace(event, text=event.text.replace("+9", "+8"),
                     event_type="edit", server_event_at=NOW + 30)
    with state_module.use_identity(query_tests.ID):
        assert asyncio.run(wanxin.handle_wanxin_reply(edited.text, NOW + 31, event=edited))
    assert env.identity["concubine_affinity"] == 208
    env.clock[0] = NOW + 40
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(manual_panel(env, route, at=NOW + 20, root=query_tests.ROOT + 100))
    assert env.identity == before
    assert asyncio.run(manual_panel(env, route, at=NOW + 35, root=query_tests.ROOT + 200))
    assert env.identity["concubine_affinity"] == 184


@pytest.mark.parametrize("corruption", ["missing", "not_dict", "invalid", "unhandled", "future", "unrelated"])
def test_snapshot_check_requires_a_valid_handled_nonfuture_affinity_point(query_env, corruption):
    env = query_env
    prepare_partner(env.identity)
    asyncio.run(apply_wanxin(query_tests.ID, "moon_greet"))
    observed = copy.deepcopy(env.identity["wanxin_observation"])
    point = observed["reply_points"]["moon_greet"]
    if corruption == "missing":
        observed.pop("reply_points")
    elif corruption == "not_dict":
        observed["reply_points"] = []
    elif corruption == "invalid":
        point["command_point"]["evidence"]["chat_id"] = 0
    elif corruption == "unhandled":
        observed["reply_points"]["moon_greet"] = {
            key: value for key, value in point.items()
            if key in {"point", "command_point", "root", "fingerprint", "handled"}
        }
        observed["reply_points"]["moon_greet"]["handled"] = False
    elif corruption == "future":
        point["point"]["at"] = NOW + 1000
    else:
        point["type"] = "cooldown"
    before = copy.deepcopy(observed)
    assert not wanxin.wanxin_affinity_snapshot_is_stale(observed, NOW + 1, now=NOW + 10)
    assert observed == before


def test_failed_wanxin_commit_does_not_leave_a_snapshot_barrier(query_env):
    env = query_env
    prepare_partner(env.identity)
    text = HEADERS["moon_greet"] + "\n" + GAIN
    event = replace(native_reply(
        query_tests.ID, wanxin.CMD_WANXIN_MOON_GREET, text, NOW + 3,
        root=query_tests.ROOT + 10, command_at=NOW + 2, chat=query_tests.CHAT,
    ), sender_id=query_tests.BOT)
    before = copy.deepcopy(env.identity)
    wanxin.save_state.return_value = False
    with state_module.use_identity(query_tests.ID):
        assert not asyncio.run(wanxin.handle_wanxin_reply(text, NOW + 4, event=event))
    assert env.identity == before
    env.clock[0] = NOW + 10
    assert asyncio.run(manual_panel(env, "native"))
    assert env.identity["concubine_affinity"] == 184


@pytest.mark.parametrize("stage", ["session", "result"])
def test_miniapp_read_detects_affinity_round_trip_during_await(cave_env, monkeypatch, stage):
    env = cave_env
    prepare_partner(env.identity)
    state_module.set_game_group_id(query_tests.CHAT)
    state_module.set_game_bot_ids([query_tests.BOT])
    clock = [NOW]
    monkeypatch.setattr(cave.time, "time", lambda: clock[0])
    env.flow.return_value["data"] = cave_tests.payload(cave_tests.CONCUBINE)
    selected = env.session if stage == "session" else env.flow

    async def result(*_args, **kwargs):
        assert kwargs["operation_check"]()
        event = await apply_wanxin(cave_tests.ID, "moon_greet")
        edited = replace(event, text=event.text.replace("+9", "+0"),
                         event_type="edit", server_event_at=NOW + 4)
        with state_module.use_identity(cave_tests.ID):
            assert await wanxin.handle_wanxin_reply(edited.text, NOW + 5, event=edited)
        assert env.identity["concubine_affinity"] == 200
        clock[0] = NOW + 5
        return selected.return_value

    selected.side_effect = result
    response = asyncio.run(cave_tests.read("concubine"))
    assert env.identity["concubine_affinity"] == 200
    assert not response["ok"]
    assert response["extra"]["status"] == "cancelled"
    assert env.identity["concubine_last_snapshot_at"] == NOW - 7200
    assert env.flow.await_count == (stage == "result")

    selected.side_effect = None
    clock[0] = NOW + 10
    env.flow.return_value["data"] = cave_tests.payload(cave_tests.CONCUBINE.replace("184", "200"))
    assert asyncio.run(cave.run_cave_public_tianjige_read_only(
        cave_tests.ID, cave_tests.ENTRY, cave_tests.COMMANDS["concubine"], now=clock[0],
    ))["ok"]
    assert env.identity["concubine_last_snapshot_at"] == clock[0]


@pytest.mark.parametrize("stage", ["session", "result"])
def test_unrelated_same_owner_wanxin_reply_keeps_miniapp_read_admitted(cave_env, stage):
    env = cave_env
    prepare_partner(env.identity)
    state_module.set_game_group_id(query_tests.CHAT)
    state_module.set_game_bot_ids([query_tests.BOT])
    env.flow.return_value["data"] = cave_tests.payload(cave_tests.CONCUBINE)
    selected = env.session if stage == "session" else env.flow

    async def result(*_args, **kwargs):
        assert kwargs["operation_check"]()
        await apply_wanxin(cave_tests.ID, "visit")
        assert kwargs["operation_check"]()
        return selected.return_value

    selected.side_effect = result
    assert asyncio.run(cave_tests.read("concubine"))["ok"]
    assert env.identity["concubine_affinity"] == 184


@pytest.mark.parametrize("route", ["native", "passive"])
def test_accepted_small_server_clock_skew_still_protects_affinity(query_env, route):
    env = query_env
    prepare_partner(env.identity)
    text = HEADERS["moon_greet"] + "\n" + GAIN
    event = replace(native_reply(
        query_tests.ID, wanxin.CMD_WANXIN_MOON_GREET, text, NOW + 3,
        root=query_tests.ROOT + 10, command_at=NOW + 2, chat=query_tests.CHAT,
    ), sender_id=query_tests.BOT)
    env.clock[0] = NOW + 2.5
    with state_module.use_identity(query_tests.ID):
        assert asyncio.run(wanxin.handle_wanxin_reply(text, env.clock[0], event=event))
    before = copy.deepcopy(env.identity)
    handled = asyncio.run(manual_panel(env, route))
    assert env.identity["concubine_affinity"] == 209
    assert not handled
    assert env.identity == before
