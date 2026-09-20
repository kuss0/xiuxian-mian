import asyncio
import copy
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import cave_treasure_runtime as cave
from model.features import cave_treasure_miniapp as miniapp
from model.features import tianjige_transport as transport
from model.features import tianxing, wild_training
from model.features import concubine

IDENTITY = 990440001
ACCOUNT = 7441
PLAYER = -1_000_000_000_000 - IDENTITY
ENTRY = "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE44"
PANEL = "【天机盘】\n今日可选命星: 【太阴】、【紫微】\n今日已定命星: 太阴\n当前推命: 无\n当前改命: 无\n天机值: 40\n逆命劫: 0\n命中 / 落空 / 改命: 190 / 1 / 43"


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    state_module.update_send_as_profile(IDENTITY, sect_name="天星宗", enabled=True)
    state_module.set_miniapp_auto_config({
        "cave_public_entry_urls": [ENTRY],
        "cave_public_concubine_enabled": True,
        "cave_public_concubine_identity_ids": [IDENTITY],
    })
    monkeypatch.setattr(tianxing, "save_state", Mock(return_value=True))
    monkeypatch.setattr(wild_training, "save_state", Mock(return_value=True))
    monkeypatch.setattr(concubine, "save_state", Mock(return_value=True))
    monkeypatch.setattr(concubine, "send_game_command", AsyncMock())
    monkeypatch.setattr(tianxing, "send_game_command", AsyncMock())
    monkeypatch.setattr(cave, "_PUBLIC_ENTRY_LOCKS", {})
    monkeypatch.setattr(cave, "_capture_store", Mock(return_value=None))
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", AsyncMock(return_value={
        "ok": True, "player_id": PLAYER, "init_data": "fixture-init",
    }))
    monkeypatch.setattr(cave, "run_cave_tianjige_command_production_flow", AsyncMock(return_value={
        "ok": True, "action_dispatched": True, "data": {
            "ok": True, "account": {"playerId": PLAYER},
            "actionResult": {"ok": True, "completed": True, "rawMessage": PANEL},
        },
    }))
    monkeypatch.setattr(cave, "run_cave_dwelling_snapshot_production_flow", AsyncMock(return_value={
        "ok": True, "data": {
            "ok": True, "account": {"playerId": PLAYER},
            "dwelling": {"companions": [{
                "name": "凌玉灵", "status": "随行中", "active": True,
                "isStarPalace": True, "greetedToday": False,
                "raw": {"name": "凌玉灵", "affection": 270},
            }]},
        },
    }))
    with state_module.use_identity(IDENTITY):
        state_module.get_identity_state(IDENTITY).update(tianxing_enabled=True, tianxing_auto_config={
            "timeline_enabled": True, "timeline_dry_run_enabled": False,
            "auto_predict_enabled": True, "auto_change_fate_enabled": True,
        }, tianxing_observation={}, tianxing_timeline_state={})
        yield state_module.state
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def run(command=".天机盘", check=lambda: True):
    return asyncio.run(transport.execute(IDENTITY, command, op_id="test-op", operation_check=check))


def test_http_receipt_has_no_telegram_ids(env):
    result = run()
    assert result["terminal"]
    assert result["receipt"]["player_id"] == PLAYER
    assert result["receipt"]["account_id"] == ACCOUNT
    assert "msg_id" not in result["receipt"]
    cave.run_cave_tianjige_command_production_flow.assert_awaited_once()
    tianxing.send_game_command.assert_not_awaited()


def test_pavilion_read_has_strict_identity_receipt(env):
    result = asyncio.run(transport.read_pavilion(
        IDENTITY, partner="凌玉灵", op_id="pavilion-op", operation_check=lambda: True,
    ))
    assert result["ok"] and result["companion"] == {
        "partner": "凌玉灵", "greeted_today": False, "affinity": 270,
    }
    assert transport.pavilion_receipt_matches(
        result["receipt"], identity_id=IDENTITY, account_id=ACCOUNT,
        op_id="pavilion-op", started_at=result["receipt"]["started_at"],
    )
    assert "msg_id" not in result["receipt"]
    assert cave.run_cave_dwelling_snapshot_production_flow.await_args.kwargs["endpoint"] == "section"
    assert cave.run_cave_dwelling_snapshot_production_flow.await_args.kwargs["section"] == "pavilion"


@pytest.mark.parametrize("change", ["owner", "duplicate", "inactive", "kind", "greeted", "affinity", "missing"])
def test_pavilion_read_rejects_unowned_or_incomplete_companion(env, change):
    data = cave.run_cave_dwelling_snapshot_production_flow.return_value["data"]
    item = data["dwelling"]["companions"][0]
    if change == "owner":
        data["account"]["playerId"] = PLAYER - 1
    elif change == "duplicate":
        data["dwelling"]["companions"].append(copy.deepcopy(item))
    elif change == "inactive":
        item["active"] = False
    elif change == "kind":
        item["isStarPalace"] = False
    elif change == "greeted":
        item["greetedToday"] = 1
    elif change == "affinity":
        item["raw"]["affection"] = "270"
    else:
        data["dwelling"].pop("companions")
    result = asyncio.run(transport.read_pavilion(
        IDENTITY, partner="凌玉灵", op_id="pavilion-op", operation_check=lambda: True,
    ))
    assert not result["ok"] and "receipt" not in result


@pytest.mark.parametrize("field,value", [
    ("section", "inventory"), ("op_id", "foreign"), ("send_as_id", IDENTITY + 1),
    ("account_id", ACCOUNT + 1), ("player_id", True), ("started_at", 0),
])
def test_pavilion_receipt_rejects_fabricated_fields(env, field, value):
    result = asyncio.run(transport.read_pavilion(
        IDENTITY, partner="凌玉灵", op_id="pavilion-op", operation_check=lambda: True,
    ))
    receipt = dict(result["receipt"])
    receipt[field] = value
    assert not transport.pavilion_receipt_matches(
        receipt, identity_id=IDENTITY, account_id=ACCOUNT,
        op_id="pavilion-op", started_at=result["receipt"]["started_at"],
    )


def test_pavilion_section_flow_is_one_read_only_request(env):
    calls = []
    def http(request):
        calls.append(request)
        return {
            "ok": True, "account": {"playerId": PLAYER},
            "dwelling": {"companions": []},
        }
    result = asyncio.run(miniapp.run_cave_dwelling_snapshot_production_flow(
        IDENTITY, token="df_FIXTURE44", webview_url=ENTRY, endpoint="section", section="pavilion",
        init_data="fixture-init", player_id=PLAYER, transport=http, operation_check=lambda: True,
    ))
    assert result["ok"], result
    assert len(calls) == 1
    assert calls[0]["payload"]["section"] == "pavilion"
    assert calls[0]["payload"]["playerId"] == PLAYER


def fragment_raw():
    return {
        "name": "凌玉灵", "affection": 270,
        "xutian_fragment_bag": {
            "xutian_chart_east": 1, "xutian_chart_north": 0,
            "xutian_chart_south": 2, "xutian_chart_west": 1,
        },
        "cangkun_fragment_bag": {
            "cangkun_chart_gate": 0, "cangkun_chart_jade": 2,
            "cangkun_chart_mulan": 0, "cangkun_chart_taimiao": 1,
        },
        "xutian_puzzle_completed": 2, "cangkun_puzzle_completed": 3,
        "last_dream_map_seek_time": "2026-09-19T18:08:21.906219+00:00",
        "last_xutian_puzzle_time": "2026-09-08T08:33:35.550358+00:00",
        "last_cangkun_puzzle_time": "2026-09-13T00:27:47.164032+00:00",
    }


def test_pavilion_fragment_projection_uses_unique_pieces_and_server_times(env):
    item = cave.run_cave_dwelling_snapshot_production_flow.return_value["data"]["dwelling"]["companions"][0]
    item.update(isStarPalace=False, raw=fragment_raw())
    result = asyncio.run(transport.read_pavilion(
        IDENTITY, partner="凌玉灵", op_id="fragment-op", operation_check=lambda: True,
        projection="fragments",
    ))
    assert result["ok"] and result["fragments"]["fragments"] == {
        "xutian": [3, 4], "cangkun": [2, 4],
    }
    assert result["fragments"]["puzzle_completed"] == {"xutian": 2, "cangkun": 3}
    assert result["fragments"]["last_dream_at"] == datetime(
        2026, 9, 19, 18, 8, 21, 906219, tzinfo=timezone.utc,
    ).timestamp()
    assert transport.fragment_snapshot_valid(result["fragments"], partner="凌玉灵")


@pytest.mark.parametrize("change", ["bag_keys", "bag_bool", "counter", "naive_time", "partner", "duplicate"])
def test_pavilion_fragment_projection_rejects_ambiguous_schema(env, change):
    data = cave.run_cave_dwelling_snapshot_production_flow.return_value["data"]
    item = data["dwelling"]["companions"][0]
    item.update(isStarPalace=False, raw=fragment_raw())
    if change == "bag_keys":
        item["raw"]["xutian_fragment_bag"]["unknown"] = 1
    elif change == "bag_bool":
        item["raw"]["xutian_fragment_bag"]["xutian_chart_east"] = True
    elif change == "counter":
        item["raw"]["cangkun_puzzle_completed"] = -1
    elif change == "naive_time":
        item["raw"]["last_dream_map_seek_time"] = "2026-09-19T18:08:21"
    elif change == "partner":
        item["name"] = "其他侍妾"
    else:
        data["dwelling"]["companions"].append(copy.deepcopy(item))
    result = asyncio.run(transport.read_pavilion(
        IDENTITY, partner="凌玉灵", op_id="fragment-op", operation_check=lambda: True,
        projection="fragments",
    ))
    assert not result["ok"] and "receipt" not in result


def test_disabled_and_frozen_are_distinct(env):
    state_module.set_identity_enabled(IDENTITY, False)
    assert not transport.available(IDENTITY)
    assert run()["action_dispatched"] is False
    state_module.set_channel_send_as_health({"status": "closed", "restore_identity_ids": [IDENTITY]})
    assert transport.available(IDENTITY)
    assert run()["terminal"]
    assert not wild_training._defer_frozen_tianxing_route(time.time(), {"route_allowed": False})


def test_pavilion_transport_requires_master_switch_and_identity_allowlist(env):
    assert transport.available(IDENTITY)
    assert transport.pavilion_available(IDENTITY)
    state_module.set_miniapp_auto_config({"cave_public_entry_urls": [ENTRY]})
    assert transport.available(IDENTITY)
    assert not transport.pavilion_available(IDENTITY)
    state_module.set_miniapp_auto_config({
        "cave_public_entry_urls": [ENTRY],
        "cave_public_concubine_enabled": True,
        "cave_public_concubine_identity_ids": [IDENTITY + 1],
    })
    assert transport.available(IDENTITY)
    assert not transport.pavilion_available(IDENTITY)


def test_unknown_is_not_retried(env):
    cave.run_cave_tianjige_command_production_flow.return_value = {
        "ok": False, "status": "failed", "action_dispatched": True, "outcome_unknown": True,
    }
    result = run(".推命 探索")
    assert result["outcome_unknown"]
    cave.run_cave_tianjige_command_production_flow.assert_awaited_once()
    tianxing.send_game_command.assert_not_awaited()


@pytest.mark.parametrize("command", [concubine.CMD_CONCUBINE_DREAM, concubine.CMD_CONCUBINE_PUZZLE])
def test_fragment_mutations_are_explicitly_whitelisted(env, command):
    result = run(command)
    assert result["terminal"] and result["receipt"]["command"] == command
    assert cave.run_cave_tianjige_command_production_flow.await_args.kwargs["command"] == command


def test_plan_changed_during_login_does_not_dispatch(env):
    current = [True]
    async def load(*args, **kwargs):
        current[0] = False
        return {"ok": True, "player_id": PLAYER, "init_data": "fixture-init"}
    cave._load_cave_public_identity_session.side_effect = load
    assert run(check=lambda: current[0])["action_dispatched"] is False
    cave.run_cave_tianjige_command_production_flow.assert_not_awaited()


def test_auto_panel_closes_http_pending(env):
    now = time.time()
    observed = tianxing.normalize_tianxing_observation({})
    config = tianxing.normalize_tianxing_auto_config(env["tianxing_auto_config"])
    assert asyncio.run(tianxing._execute_tianxing_auto_plan(
        {"action": "panel", "command": ".天机盘"}, observed, config, now,
    ))
    assert env["tianxing_observation"]["tianji_value"] == 40
    assert env["tianxing_observation"]["auto_pending_action"] == ""
    tianxing.send_game_command.assert_not_awaited()


def seed_timeline(env, now):
    env["tianxing_observation"] = tianxing.normalize_tianxing_observation({
        "last_observed_at": now - 30, "fixed_star": "太阴", "fixed_star_day": tianxing.get_day_key(now),
        "tianji_value": 40,
    })
    step = {"action": "predict", "arg": "探索", "route": "探索", "command": ".推命 探索", "status": "pending"}
    timeline = tianxing.normalize_tianxing_timeline_state({
        "plan_id": "test-plan", "created_at": now, "phase": "waiting_send", "route": "探索",
        "active_step_index": 0, "active_step": step, "steps": [step],
    })
    env["tianxing_timeline_state"] = timeline
    return timeline, step


def send_step(env):
    now = time.time()
    timeline, step = seed_timeline(env, now)
    return asyncio.run(tianxing._send_tianxing_timeline_step(
        timeline, step, now, tianxing.normalize_tianxing_auto_config(env["tianxing_auto_config"]),
        operation=tianxing._TianxingOperation.capture(now),
    ))


def test_timeline_predict_confirmed_from_http(env):
    cave.run_cave_tianjige_command_production_flow.return_value["data"]["actionResult"]["rawMessage"] = (
        "你为【探索】推下了一段命数。\n有效期：8小时。"
    )
    result = send_step(env)
    assert result["active_step"]["status"] == "confirmed"
    assert result["active_step"]["send_msg_id"] == 0
    assert env["tianxing_observation"]["current_prediction"] == "探索"
    tianxing.send_game_command.assert_not_awaited()


def test_unknown_timeline_keeps_pending(env):
    cave.run_cave_tianjige_command_production_flow.return_value = {
        "ok": False, "action_dispatched": True, "outcome_unknown": True,
    }
    result = send_step(env)
    assert result["active_step"]["status"] == "ack_timeout"
    assert result["active_step"]["send_transport"] == "miniapp"
    assert tianxing._tianxing_timeline_mutation_pending(result)
    tianxing.send_game_command.assert_not_awaited()


@pytest.mark.parametrize("present", [True, False])
def test_timeline_unknown_uses_fresh_panel_without_resending(env, present):
    response = copy.deepcopy(cave.run_cave_tianjige_command_production_flow.return_value)
    cave.run_cave_tianjige_command_production_flow.return_value = {
        "ok": False, "action_dispatched": True, "outcome_unknown": True,
    }
    pending = send_step(env)
    now = time.time()
    timeline = tianxing._schedule_tianxing_timeline_calibration(pending, now)
    env["tianxing_timeline_state"] = timeline
    if present:
        response["data"]["actionResult"]["rawMessage"] = PANEL.replace("当前推命: 无", "当前推命: 探索（剩余 7小时）")
    cave.run_cave_tianjige_command_production_flow.return_value = response
    result = asyncio.run(tianxing._send_tianxing_timeline_step(
        timeline, timeline["active_step"], now, tianxing.normalize_tianxing_auto_config(env["tianxing_auto_config"]),
        operation=tianxing._TianxingOperation.capture(now),
    ))
    assert result["steps"][0]["status"] == ("confirmed" if present else "calibration_not_confirmed")
    assert cave.run_cave_tianjige_command_production_flow.await_args.kwargs["command"] == ".天机盘"
    tianxing.send_game_command.assert_not_awaited()


def test_tianxing_stale_prediction_reply_does_not_revive_consumed_effect(env):
    async def changed(*args, **kwargs):
        env["tianxing_observation"]["prediction_consumed_at"] = time.time()
        return {"ok": True, "action_dispatched": True, "data": {"ok": True, "account": {"playerId": PLAYER},
            "actionResult": {"ok": True, "completed": True, "rawMessage": "你为【探索】推下了一段命数。有效期8小时。"}}}
    cave.run_cave_tianjige_command_production_flow.side_effect = changed
    result = send_step(env)
    assert result["active_step"]["status"] == "ack_timeout"
    assert not env["tianxing_observation"]["current_prediction"]


def test_tianxing_failed_projection_save_cannot_release_prediction(env):
    cave.run_cave_tianjige_command_production_flow.return_value["data"]["actionResult"]["rawMessage"] = (
        "你为【探索】推下了一段命数。\n有效期：8小时。"
    )
    tianxing.save_state.side_effect = [True, False, True]
    result = send_step(env)
    assert result["active_step"]["status"] == "ack_timeout"
    assert not env["tianxing_observation"]["current_prediction"]
    tianxing.send_game_command.assert_not_awaited()


@pytest.mark.parametrize("present", [True, False])
def test_auto_unknown_calibrates_only_existing_matching_effect(env, present):
    now = time.time()
    observed = tianxing.normalize_tianxing_observation({})
    tianxing._note_tianxing_auto_pending(observed, now - 120,
        {"action": "predict", "command": ".推命 探索"}, {})
    observed["auto_pending_transport"] = "miniapp"
    observed["auto_pending_due_at"] = now - 1
    env["tianxing_observation"] = observed
    if present:
        cave.run_cave_tianjige_command_production_flow.return_value["data"]["actionResult"]["rawMessage"] = (
            PANEL.replace("当前推命: 无", "当前推命: 探索（剩余 7小时）"))
    assert asyncio.run(tianxing._recover_tianxing_miniapp_auto_pending(now, tianxing._TianxingOperation.capture(now)))
    assert env["tianxing_observation"]["auto_pending_action"] == ("" if present else "predict")
    assert cave.run_cave_tianjige_command_production_flow.await_args.kwargs["command"] == ".天机盘"
    tianxing.send_game_command.assert_not_awaited()


def test_fabricated_receipt_cannot_bind(env):
    now = time.time()
    observed = tianxing.normalize_tianxing_observation({})
    tianxing._note_tianxing_auto_pending(observed, now, {"action": "panel", "command": ".天机盘"}, {})
    observed["auto_pending_transport"] = "miniapp"
    env["tianxing_observation"] = observed
    assert not tianxing.apply_tianxing_passive(PANEL, now=now + 1, reply_context={
        "transport": "miniapp", "op_id": observed["auto_pending_op_id"], "command": ".天机盘",
        "account_id": ACCOUNT, "send_as_id": IDENTITY, "player_id": PLAYER,
    })


def test_pavilion_status_uses_existing_reducer_without_reply_anchor(env):
    state_module.get_identity_state(IDENTITY).update(concubine_enabled=True, concubine_phase="idle")
    cave.run_cave_tianjige_command_production_flow.return_value["data"]["actionResult"]["rawMessage"] = (
        "你的道心侍妾：【南宫婉】（状态：随行中）\n情缘值：184\n"
        "入梦寻图冷却：2小时\n天机代卜冷却：3小时\n共历心劫冷却：4小时"
    )
    assert asyncio.run(concubine._send_pavilion_status(time.time()))
    assert env["concubine_name"] == "南宫婉"
    assert env["concubine_affinity"] == 184
    assert env["concubine_last_panel_msg_id"] == 0
    concubine.send_game_command.assert_not_awaited()


def test_pavilion_pending_heart_prevents_status_request(env):
    state_module.get_identity_state(IDENTITY).update(concubine_enabled=True, concubine_phase="heart_pending")
    assert not asyncio.run(concubine._send_pavilion_status(time.time()))
    cave.run_cave_tianjige_command_production_flow.assert_not_awaited()


def test_pavilion_unparsed_result_never_falls_back_to_group(env):
    state_module.get_identity_state(IDENTITY).update(concubine_enabled=True, concubine_phase="idle")
    assert not asyncio.run(concubine._send_pavilion_status(time.time()))
    assert env["next_concubine_time"] > time.time()
    concubine.send_game_command.assert_not_awaited()


def test_pavilion_live_moon_panel_preserves_missing_affinity(env):
    state_module.get_identity_state(IDENTITY).update(
        concubine_enabled=True, concubine_phase="idle", concubine_name="南宫婉·月影",
        concubine_kind="红尘道侣", concubine_affinity=184,
    )
    # Real 2026-09-19 public-entry reply omits affinity for a red-dust companion.
    cave.run_cave_tianjige_command_production_flow.return_value["data"]["actionResult"]["rawMessage"] = (
        "**1. 你的红尘道侣: 【南宫婉·月影】** (状态: 随行中)\n"
        "**【掩月心契】**\n- 当前誓约: **共修**\n"
        "**【第二期机缘】**\n- 天机代卜链: 无\n- 坠魔谷护持: 无\n"
        "- 入梦寻图冷却: 472分钟\n- 共历心劫冷却: 592分钟\n- 天机代卜冷却: 712分钟\n"
        "- 梦图拼片: 虚天 3/4 | 苍坤 2/4\n**婉影共鸣**: 已觉醒\n"
        "常住洞府与随行均保留既有神通，沿用原情缘、消耗、冷却与远航限制。"
    )
    now = time.time()
    assert asyncio.run(concubine._send_pavilion_status(now))
    assert env["concubine_name"] == "南宫婉·月影"
    assert env["concubine_affinity"] == 184
    assert env["concubine_dream_due_at"] >= now + 472 * 60
    assert env["concubine_tianji_due_at"] >= now + 712 * 60


def test_pavilion_voyage_status_does_not_send_settlement(env):
    state_module.get_identity_state(IDENTITY).update(
        concubine_voyage_enabled=True, concubine_phase="idle", concubine_name="南宫婉·月影",
        concubine_voyage_status="needs_status", next_concubine_time=0,
    )
    state_module._meta_state["game_group_id"] = -1001234
    cave.run_cave_tianjige_command_production_flow.return_value["data"]["actionResult"]["rawMessage"] = (
        "远航状态: 月殿寻痕航线进行中，剩余约 319 分钟。"
    )
    now = time.time()
    assert asyncio.run(concubine._send_pavilion_voyage_status(now))
    assert env["concubine_voyage_status"] == "sailing"
    assert env["concubine_voyage_return_at"] >= now + 319 * 60
    assert cave.run_cave_tianjige_command_production_flow.await_args.kwargs["command"] == ".远航状态"
    concubine.send_game_command.assert_not_awaited()
