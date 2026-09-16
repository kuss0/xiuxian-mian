import asyncio
import copy
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, persistence, state as state_module
from model.features import cave_treasure_runtime as cave
from model.features import concubine, concubine_affinity_actions as affinity, passive_inbox, wanxin
from tests import test_cave_read_only_lifecycle as cave_tests
from tests import test_concubine_gift_lifecycle as gifts
from tests import test_concubine_greet_lifecycle as greets
from tests.test_concubine_query_lifecycle import PANEL
from tests.test_wanxin_reply_contract import COST, GAIN, HEADERS, SAMPLES
from tests.yinluo_native_support import native_reply


ID, ACCOUNT, CHAT, BOT, ROOT, NOW = greets.ID, greets.ACCOUNT, greets.CHAT, greets.BOT, greets.ROOT, greets.NOW
NAME = "\u5357\u5bab\u5a49"
EFFECTS = [("moon_greet", 279, 309), ("moon_seal", 246, 276)]
ROUTES = ("direct", "native", "passive")
greet_env = greets.env
gift_env = gifts.env


@pytest.fixture
def env(greet_env, monkeypatch):
    greet_env.identity["concubine_name"] = NAME
    monkeypatch.setattr(wanxin, "save_state", Mock(return_value=True))
    return greet_env


async def apply_wanxin(action, *, case=greets, at=NOW + 2):
    text = HEADERS[action] + "\n" + (GAIN if action == "moon_greet" else COST)
    event = replace(native_reply(
        case.ID, wanxin.WANXIN_ACTION_COMMANDS[action], text, at,
        root=case.ROOT + 100, command_at=at - 1, chat=case.CHAT,
    ), sender_id=case.BOT)
    with state_module.use_identity(case.ID):
        assert await wanxin.handle_wanxin_reply(text, at + 1, event=event)


async def complete_greet(env, action=None):
    assert await greets.send_greet(env)
    greets.receipt(env)
    if action:
        await apply_wanxin(action)
    env.clock[0] = NOW + 3
    assert await greets.reply(env, text=greets.SUCCESS.replace(greets.NAME, NAME),
                             observed_at=NOW + 3, current_msg_id=ROOT + 200)
    assert env.identity[greets.FIELD]["status"] == "complete"
    assert not env.identity["pending_tasks"]


async def status(env, route, value, *, command_at, at=NOW + 5, root=None, case=greets):
    root = case.ROOT + 300 if root is None else root
    text = PANEL.replace("184", str(value)).replace(greets.NAME, NAME)
    event = SimpleNamespace(id=case.ROOT + 500, chat_id=case.CHAT, sender_id=case.BOT, server_event_at=at)
    parent = SimpleNamespace(id=root, chat_id=case.CHAT, sender_id=case.ID, raw_text=concubine.CMD_CONCUBINE_STATUS,
                             server_event_at=command_at, edit_date=None)
    context = {
        "send_as_id": case.ID, "account_id": case.ACCOUNT, "chat_id": case.CHAT, "family": "concubine_status",
        "root_msg_id": root, "reply_to_msg_id": root, "reply_to_sender_id": case.ID,
        "reply_to_command": concubine.CMD_CONCUBINE_STATUS,
        "reply_to_server_at": command_at, "reply_to_command_edited": False,
    }
    if route == "native":
        return await app._handle_routed_reply_event(event, text, NOW + 10, parent, context)
    if route == "passive":
        return await passive_inbox.handle_passive_module_card(
            text, now=NOW + 10, event=event, reply_context=context, event_type="message",
        )
    with state_module.use_identity(case.ID):
        return await concubine.handle_concubine_status_reply(
            text, NOW + 10, parent, matched_family="concubine_status", current_msg_id=event.id,
            current_chat_id=case.CHAT, observed_at=at, reply_context=dict(context, sender_id=case.BOT),
        )


@pytest.mark.parametrize("action,unmerged,expected", EFFECTS)
@pytest.mark.parametrize("consumer", ["cached_panel", "moon_seal", "partner"])
def test_unmerged_owned_gain_is_not_spendable(env, action, unmerged, expected, consumer):
    asyncio.run(complete_greet(env, action))
    assert env.identity["concubine_affinity"] == unmerged
    assert not env.identity[greets.FIELD]["result"]["applied"]
    with state_module.use_identity(ID):
        if consumer == "cached_panel":
            allowed = concubine._can_use_cached_panel_for_gift_recovery(NOW + 4)
        elif consumer == "partner":
            allowed = concubine._has_available_partner()
        else:
            observation = wanxin.normalize_wanxin_observation(env.identity["wanxin_observation"])
            observation["moon_awakened"] = True
            observation["auto_config"]["moon_seal_enabled"] = True
            allowed = wanxin._action_enabled(observation, "moon_seal")
    assert not allowed


@pytest.mark.parametrize("action,unmerged,expected", EFFECTS)
def test_due_scheduler_reads_status_instead_of_spending_an_unmerged_gain(env, action, unmerged, expected):
    asyncio.run(complete_greet(env, action))
    env.identity["next_concubine_time"] = NOW + 4
    env.send.return_value = SimpleNamespace(id=ROOT + 300, chat_id=CHAT, sent_at=NOW + 4, send_started_at=NOW + 4)
    env.send.reset_mock()
    greets.tick(env, NOW + 4)
    env.send.assert_awaited_once()
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_STATUS
    assert env.identity["concubine_affinity"] == unmerged
    assert env.identity[greets.FIELD]["result"]["gain"] == 30


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("command_at,root", [(NOW, ROOT - 100), (NOW + 3, ROOT + 300)])
def test_old_or_unordered_query_cannot_erase_completed_owned_gain(env, route, command_at, root):
    asyncio.run(complete_greet(env))
    assert env.identity["concubine_affinity"] == 300
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(status(env, route, 270, command_at=command_at, root=root))
    assert env.identity == before


@pytest.mark.parametrize("action,unmerged,expected", EFFECTS)
@pytest.mark.parametrize("route", ROUTES)
def test_later_absolute_read_reconciles_without_reapplying_the_owned_gain(env, action, unmerged, expected, route):
    asyncio.run(complete_greet(env, action))
    record = copy.deepcopy(env.identity[greets.FIELD])
    assert asyncio.run(status(env, route, expected, command_at=NOW + 4))
    assert env.identity["concubine_affinity"] == expected
    assert env.identity[greets.FIELD] == record
    with state_module.use_identity(ID):
        assert concubine._has_available_partner()
    env.send.assert_awaited_once()


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("action,unmerged,expected", EFFECTS)
def test_gift_gain_and_inventory_completion_remain_separate_from_affinity_calibration(gift_env, monkeypatch, route, action, unmerged, expected):
    env = gift_env
    env.identity["concubine_name"] = NAME
    monkeypatch.setattr(wanxin, "save_state", Mock(return_value=True))
    gifts.prepare_gift(env, monkeypatch)
    assert asyncio.run(gifts.send_gift(env))
    gifts.receipt(env, "gift", gifts.ROOT + 10)
    asyncio.run(apply_wanxin(action, case=gifts, at=NOW + 4))
    env.clock[0] = NOW + 5
    assert asyncio.run(gifts.reply(env, "gift", text=gifts.SUCCESS.replace(gifts.NAME, NAME),
                                  observed_at=NOW + 5, current_msg_id=gifts.ROOT + 200))
    assert not env.identity["pending_tasks"]
    assert env.identity["concubine_affinity"] == unmerged - 30
    record = copy.deepcopy(env.identity[gifts.FIELD])
    assert record["gift"]["status"] == "complete"
    assert not record["gift"]["result"]["applied"]
    assert state_module.get_storage_bag_records()[str(gifts.ID)]["items"][gifts.STONE] == 940
    with state_module.use_identity(gifts.ID):
        assert affinity.needs_calibration()
        assert not concubine._has_available_partner()
    assert asyncio.run(status(env, route, expected, command_at=NOW + 6, at=NOW + 7, case=gifts))
    assert env.identity["concubine_affinity"] == expected
    assert env.identity[gifts.FIELD] == record
    with state_module.use_identity(gifts.ID):
        assert not affinity.needs_calibration()
    assert state_module.get_storage_bag_records()[str(gifts.ID)]["items"][gifts.STONE] == 940
    env.send.assert_awaited_once()


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("failure", ["false", "exception", "sqlite"])
def test_calibration_failure_preserves_completed_gain_and_can_replay(env, monkeypatch, route, failure):
    asyncio.run(complete_greet(env, "moon_greet"))
    before = copy.deepcopy(env.identity)
    if failure == "sqlite":
        assert persistence.save_state()
        monkeypatch.setattr(concubine, "save_state", persistence.save_state)
        conn = persistence.get_db_conn()
        conn.execute(f"CREATE TEMP TRIGGER fail_owned_affinity BEFORE INSERT ON identity_runtime_state "
                     f"WHEN NEW.send_as_id = {ID} BEGIN SELECT RAISE(ABORT, 'affinity failure'); END")
    elif failure == "exception":
        env.save.side_effect = OSError("affinity failure")
    else:
        env.save.return_value = False
    try:
        if failure == "exception":
            with pytest.raises(OSError, match="affinity failure"):
                asyncio.run(status(env, route, 309, command_at=NOW + 4))
        else:
            assert not asyncio.run(status(env, route, 309, command_at=NOW + 4))
        assert env.identity == before
        with state_module.use_identity(ID):
            assert affinity.needs_calibration()
    finally:
        if failure == "sqlite":
            conn.execute("DROP TRIGGER fail_owned_affinity")
    monkeypatch.setattr(concubine, "save_state", persistence.save_state)
    assert asyncio.run(status(env, route, 309, command_at=NOW + 4))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(status(env, route, 309, command_at=NOW + 4))
    assert env.identity == before
    with state_module.use_identity(ID):
        assert not affinity.needs_calibration()


def test_unprojected_gain_stays_held_after_sqlite_reload_without_changing_daily_facts(env):
    asyncio.run(complete_greet(env, "moon_greet"))
    record = copy.deepcopy(env.identity[greets.FIELD])
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    with state_module.use_identity(ID):
        assert affinity.needs_calibration()
        assert not concubine._has_available_partner()
        assert "\u60c5\u7f18\u4f59\u989d\u5f85\u6821\u51c6" in concubine.get_concubine_status_text()
    assert env.identity[greets.FIELD] == record
    assert env.identity["concubine_last_greet_day"] == concubine._local_day_key(NOW)
    assert asyncio.run(status(env, "native", 309, command_at=NOW + 4))
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    with state_module.use_identity(ID):
        assert not affinity.needs_calibration()
    assert env.identity[greets.FIELD] == record


@pytest.mark.parametrize("control", ["global", "identity", "modules", "schedule"])
def test_calibration_respects_disabled_controls_and_replacement_schedule(env, control):
    asyncio.run(complete_greet(env, "moon_greet"))
    env.identity["next_concubine_time"] = NOW + 4
    if control == "global":
        state_module.set_global_enabled(False)
    elif control == "identity":
        state_module.set_identity_enabled(ID, False)
    elif control == "modules":
        env.identity.update(concubine_enabled=False, concubine_tianji_enabled=False,
                            concubine_heart_enabled=False, concubine_voyage_enabled=False)
    else:
        env.identity["next_concubine_time"] = NOW + 40000
    record = copy.deepcopy(env.identity[greets.FIELD])
    next_at = env.identity["next_concubine_time"]
    env.send.reset_mock()
    greets.tick(env, NOW + 4)
    env.send.assert_not_awaited()
    assert env.identity[greets.FIELD] == record
    assert env.identity["next_concubine_time"] == next_at


@pytest.mark.parametrize("case", ["applied", "partner", "account", "new_snapshot", "daily_limit"])
def test_unrelated_or_already_covered_completion_does_not_invent_a_calibration_hold(env, case):
    if case == "daily_limit":
        assert asyncio.run(greets.send_greet(env))
        assert asyncio.run(greets.reply(env, text=greets.ALREADY))
    else:
        asyncio.run(complete_greet(env, None if case == "applied" else "moon_greet"))
    if case == "partner":
        env.identity["concubine_name"] = "replacement"
    elif case == "account":
        state_module.set_identity_account(ID, ACCOUNT + 1)
    elif case == "new_snapshot":
        env.identity.update(concubine_last_snapshot_at=NOW + 4, concubine_affinity=309)
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not affinity.needs_calibration()
    assert env.identity == before


@pytest.mark.parametrize("at,allowed", [(NOW + 2, False), (NOW + 3, False), (NOW + 4, True)])
def test_miniapp_read_uses_request_clock_and_preserves_completion(env, monkeypatch, at, allowed):
    asyncio.run(complete_greet(env, "moon_greet"))
    record = copy.deepcopy(env.identity[greets.FIELD])
    player = ID
    loader = AsyncMock(return_value={
        "ok": True, "init_data": "fixture-init", "player_id": player,
        "result": {"ok": True, "data": {"raw": {"account": {"playerId": player}}}},
    })
    flow = AsyncMock(return_value={"ok": True, "status": "ok", "data": {
        "account": {"playerId": player}, "actionResult": {
            "ok": True, "completed": True, "rawMessage": cave_tests.CONCUBINE.replace("184", "309"),
        },
    }})
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", loader)
    monkeypatch.setattr(cave, "run_cave_tianjige_command_production_flow", flow)
    monkeypatch.setattr(cave, "_capture_store", lambda *_args: None)
    env.clock[0] = NOW + 10
    response = asyncio.run(cave.run_cave_public_tianjige_read_only(ID, cave_tests.ENTRY, concubine.CMD_CONCUBINE_STATUS, now=at))
    assert response["ok"] is allowed
    assert env.identity["concubine_affinity"] == (309 if allowed else 279)
    assert env.identity[greets.FIELD] == record
    assert loader.await_count == allowed
    assert flow.await_count == allowed


@pytest.mark.parametrize("command_at,expected", [(NOW, 300), (NOW + 3, 300), (NOW + 4, 314)])
def test_moon_panel_cannot_erase_a_newer_owned_gain(env, command_at, expected):
    asyncio.run(complete_greet(env))
    text = SAMPLES["wanxin_moon_panel"]
    event = replace(native_reply(ID, wanxin.CMD_WANXIN_MOON_STATUS, text, NOW + 5,
                                root=ROOT - 100 if command_at == NOW else ROOT + 300,
                                command_at=command_at, chat=CHAT), sender_id=BOT, msg_id=ROOT + 500)
    with state_module.use_identity(ID):
        assert asyncio.run(wanxin.handle_wanxin_reply(text, NOW + 6, event=event))
    assert env.identity["wanxin_observation"]["reply_points"]["moon_status"]["handled"]
    assert env.identity["concubine_affinity"] == expected


def test_newer_moon_panel_can_reconcile_an_unprojected_owned_gain(env):
    asyncio.run(complete_greet(env, "moon_greet"))
    text = SAMPLES["wanxin_moon_panel"].replace("314", "309")
    event = replace(native_reply(ID, wanxin.CMD_WANXIN_MOON_STATUS, text, NOW + 5,
                                root=ROOT + 300, command_at=NOW + 4, chat=CHAT), sender_id=BOT)
    env.clock[0] = NOW + 6
    with state_module.use_identity(ID):
        assert asyncio.run(wanxin.handle_wanxin_reply(text, NOW + 6, event=event))
        assert env.identity["concubine_affinity"] == 309
        assert not affinity.needs_calibration()
        assert concubine._has_available_partner()


@pytest.mark.parametrize("kind", ["missing", "unhandled", "command_edited", "root", "old_read", "equal_read",
                                  "future", "type", "clock", "chat"])
def test_moon_calibration_requires_a_valid_later_absolute_read(env, kind):
    asyncio.run(complete_greet(env, "moon_greet"))
    text = SAMPLES["wanxin_moon_panel"].replace("314", "309")
    event = replace(native_reply(ID, wanxin.CMD_WANXIN_MOON_STATUS, text, NOW + 5,
                                root=ROOT + 300, command_at=NOW + 4, chat=CHAT), sender_id=BOT)
    env.clock[0] = NOW + 6
    with state_module.use_identity(ID):
        assert asyncio.run(wanxin.handle_wanxin_reply(text, NOW + 6, event=event))
    observation = copy.deepcopy(env.identity["wanxin_observation"])
    point = observation["reply_points"]["moon_status"]
    if kind == "missing":
        observation["reply_points"].pop("moon_status")
    elif kind == "unhandled":
        observation["reply_points"]["moon_status"] = {
            key: value for key, value in point.items()
            if key in {"point", "command_point", "root", "fingerprint", "handled"}
        }
        observation["reply_points"]["moon_status"]["handled"] = False
    elif kind == "command_edited":
        point["command_point"]["evidence"]["edited"] = True
    elif kind == "root":
        point["root"] += 1
    elif kind in {"old_read", "equal_read"}:
        point["command_point"]["at"] = NOW + (2 if kind == "old_read" else 3)
    elif kind == "future":
        point["point"]["at"] = NOW + 10
    elif kind == "type":
        point["type"] = "moon_greet_success"
    elif kind == "clock":
        point["command_point"]["at"] = str(NOW + 4)
    else:
        point["command_point"]["evidence"]["chat_id"] = CHAT - 1
    env.identity["wanxin_observation"] = observation
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert affinity.needs_calibration()
        assert not concubine._has_available_partner()
    assert env.identity == before


@pytest.mark.parametrize("failure", ["false", "exception"])
def test_failed_moon_calibration_never_unlocks_spending_and_success_survives_reload(env, monkeypatch, failure):
    asyncio.run(complete_greet(env, "moon_greet"))
    text = SAMPLES["wanxin_moon_panel"].replace("314", "309")
    event = replace(native_reply(ID, wanxin.CMD_WANXIN_MOON_STATUS, text, NOW + 5,
                                root=ROOT + 300, command_at=NOW + 4, chat=CHAT), sender_id=BOT)
    env.clock[0] = NOW + 6
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        if failure == "exception":
            wanxin.save_state.side_effect = OSError("moon calibration failure")
            with pytest.raises(OSError, match="moon calibration failure"):
                asyncio.run(wanxin.handle_wanxin_reply(text, NOW + 6, event=event))
        else:
            wanxin.save_state.return_value = False
            assert not asyncio.run(wanxin.handle_wanxin_reply(text, NOW + 6, event=event))
        assert env.identity == before
        assert affinity.needs_calibration()
        monkeypatch.setattr(wanxin, "save_state", persistence.save_state)
        assert asyncio.run(wanxin.handle_wanxin_reply(text, NOW + 6, event=event))
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    with state_module.use_identity(ID):
        assert not affinity.needs_calibration()
        assert asyncio.run(wanxin.handle_wanxin_reply(text, NOW + 7, event=event))
        assert not affinity.needs_calibration()
    assert env.identity["concubine_affinity"] == 309
    assert env.identity[greets.FIELD] == before[greets.FIELD]


def test_fresh_owned_read_can_calibrate_without_replacing_completed_gain(env):
    asyncio.run(complete_greet(env, "moon_greet"))
    record = copy.deepcopy(env.identity[greets.FIELD])
    env.clock[0] = NOW + 4
    env.send.return_value = SimpleNamespace(id=ROOT + 300, chat_id=CHAT, sent_at=NOW + 4, send_started_at=NOW + 4)
    with state_module.use_identity(ID):
        assert asyncio.run(concubine._send_status_query("status", NOW + 4))
    assert asyncio.run(status(env, "native", 309, command_at=NOW + 4))
    assert env.identity["concubine_status_query"]["status"] == "complete"
    assert env.identity[greets.FIELD] == record
    assert env.identity["concubine_affinity"] == 309
    with state_module.use_identity(ID):
        assert not affinity.needs_calibration()
