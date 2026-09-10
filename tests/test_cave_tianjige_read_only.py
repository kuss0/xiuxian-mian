import asyncio
import copy
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import cave_treasure_miniapp, cave_treasure_runtime, concubine, yinluo
from model.real_message_replay import get_real_message_text
from model.webapp_core import MiniAppRequestAborted, require_miniapp_operation


NOW = 1_700_000_500.0
ENTRY = "https://t.me/fanrenxiuxian_bot?startapp=df_FIXTURE999"
PANEL = (
    "**【竹灵 2的阴罗幡】**\n"
    "**本命魔兵：** `乌龙幡`\n"
    "**幡体等阶：** 玄阶\n"
    "**煞气池：** 120 / 500 (24%)\n"
    "- **1号槽：** `[精华已成]` - 结丹修士\n"
    "- **2号槽：** `[炼化中]` - 元婴修士 (剩余：30分钟)\n"
    "- **3号槽：** `[空闲]`"
)
CONCUBINE_PANEL = "你的道心侍妾：【南宫婉】（状态：随行中）\n情缘值：184"


@pytest.fixture
def runtime(monkeypatch):
    metadata = copy.deepcopy(state_module._meta_state)
    locks = dict(cave_treasure_runtime._PUBLIC_ENTRY_LOCKS)
    state_module._meta_state.update({
        "identity_ids": [], "identity_states": {}, "send_as_profiles": {},
        "identity_account_map": {}, "channel_send_as_health": {},
    })
    cave_treasure_runtime._PUBLIC_ENTRY_LOCKS.clear()
    state_module.ensure_identity_registered(1001)
    state_module.set_identity_account(1001, 11)
    state_module.update_send_as_profile(1001, username="fixture_user", label="竹灵 2", xiuwei_current=100000)
    identity = state_module.get_identity_state(1001)
    identity["yinluo_observation"] = yinluo.normalize_yinluo_observation({
        "sha_current": 300, "sha_max": 500, "sha_percent": 60,
        "auto_next_time": NOW + 3600, "auto_last_action": "fixture_wait",
        "auto_last_error": "retain_error", "auto_calibrate_reason": "retain_reason",
        "resource_recovery_min_sha": 100,
        "soul_stocks": {"凶兽戾魄": 4},
        "soul_lineage": {"元婴修士": 3}, "banner_traits": {"守魂": "+2%"},
    })
    session = AsyncMock(return_value={"ok": True, "init_data": "fixture_init", "player_id": 1001})
    flow = AsyncMock(return_value={"ok": True, "data": {"actionResult": {"rawMessage": PANEL}}})
    save = Mock()
    audit = AsyncMock()
    monkeypatch.setattr(cave_treasure_runtime, "_public_entry_allowed", lambda: True)
    monkeypatch.setattr(cave_treasure_runtime, "_load_cave_public_identity_session", session)
    monkeypatch.setattr(cave_treasure_runtime, "run_cave_tianjige_command_production_flow", flow)
    monkeypatch.setattr(cave_treasure_runtime, "_capture_store", lambda _now: None)
    monkeypatch.setattr(cave_treasure_runtime, "send_audit_log", audit)
    monkeypatch.setattr(cave_treasure_runtime, "console_log", Mock())
    monkeypatch.setattr(yinluo, "save_state", save)
    monkeypatch.setattr(concubine, "save_state", save)
    try:
        yield SimpleNamespace(identity=identity, session=session, flow=flow, save=save, audit=audit)
    finally:
        cave_treasure_runtime._PUBLIC_ENTRY_LOCKS.clear()
        cave_treasure_runtime._PUBLIC_ENTRY_LOCKS.update(locks)
        state_module._meta_state.clear()
        state_module._meta_state.update(metadata)


def read(command=".我的阴罗幡"):
    return asyncio.run(cave_treasure_runtime.run_cave_public_tianjige_read_only(1001, ENTRY, command, now=NOW))


@pytest.mark.parametrize("message", [
    "【转化成功】\n你成功将 10000 点修为炼化，煞气池增加了 2000 点！",
    "【转化失败·反噬】\n魔功失控，你消耗的 10000 点修为尽数逸散！",
    "安抚成功！\n你消耗了 50 点修为，成功安抚了 1 个炼化槽。",
    "你引动九幽煞气灌入幡中，阴罗幡发出一阵愉悦的嘶鸣！你的煞气池增加了 500 点。",
    "一缕【凶兽戾魄】被强行打入7号炼化槽，在煞气的包裹下发出阵阵哀嚎，炼化已开始。",
    "你的煞气不足！炼化需要消耗 1000 点煞气。",
], ids=["convert", "convert_backlash", "soothe", "sacrifice", "refine", "refine_shortage"])
def test_read_only_banner_cannot_process_mutation_results(runtime, message):
    before = copy.deepcopy(runtime.identity)
    profile = copy.deepcopy(state_module.get_send_as_profile(1001))
    runtime.flow.return_value["data"]["actionResult"]["rawMessage"] = message
    response = read()
    assert state_module.get_send_as_profile(1001) == profile
    assert runtime.identity == before
    assert not response["ok"]
    runtime.save.assert_not_called()


def test_banner_read_updates_panel_only_without_rearming_scheduler(runtime):
    before = copy.deepcopy(runtime.identity["yinluo_observation"])
    response = read()
    assert response["ok"]
    observed = runtime.identity["yinluo_observation"]
    assert observed["sha_current"] == 120
    assert observed["ready_slot_numbers"] == [1]
    assert observed["refining_slot_numbers"] == [2]
    assert observed["empty_slot_numbers"] == [3]
    for key, value in before.items():
        if key.startswith("auto_") or key.startswith("next_") or key in {
            "resource_recovery_min_sha", "soul_stocks", "soul_lineage", "banner_traits",
        }:
            assert observed[key] == value, key
    assert state_module.get_send_as_profile(1001)["xiuwei_current"] == 100000
    runtime.save.assert_called_once_with()


@pytest.mark.parametrize("pending", [
    {"auto_collect_pending": {"slots": [1], "sent_at": NOW - 10}},
    {"auto_refine_pending": {"slot": 1, "sent_at": NOW - 10, "sha_current": 300}},
    {"auto_soothe_pending": {"slot": 1, "sent_at": NOW - 10}},
    {"last_result": "pending", "last_action": "化功为煞"},
    {"last_result": "pending", "last_action": "召唤魔影"},
    {"last_result": "pending", "last_action": "血洗山林"},
], ids=["collect", "refine", "soothe", "convert", "summon", "forest"])
def test_banner_read_does_not_complete_or_replace_pending_mutation(runtime, pending):
    runtime.identity["yinluo_observation"].update(pending)
    before = copy.deepcopy(runtime.identity)
    response = read()
    assert not response["ok"]
    assert runtime.identity == before
    runtime.save.assert_not_called()


@pytest.mark.parametrize("message", [
    "【竹灵 2的阴罗幡】",
    "【竹灵 2的阴罗幡】\n煞气池：300 / 500 (60%)",
    "【竹灵 2的阴罗幡】\n- 1号槽：[空闲]",
    PANEL + "\n- 1号槽：[炼化中] - 元婴修士 (剩余：1小时)",
    PANEL.replace("120 / 500", "120 / 0"),
    PANEL.replace("[空闲]", "[未知状态]"),
], ids=["title_only", "pool_only", "slots_only", "duplicate_slot", "invalid_max", "unknown_slot"])
def test_incomplete_or_ambiguous_banner_keeps_existing_snapshot(runtime, message):
    before = copy.deepcopy(runtime.identity)
    runtime.flow.return_value["data"]["actionResult"]["rawMessage"] = message
    assert not read()["ok"]
    assert runtime.identity == before
    runtime.save.assert_not_called()


@pytest.mark.parametrize("stage", ["session", "result"])
@pytest.mark.parametrize("change", ["delete", "replace", "rebind", "disable", "pause"])
def test_read_only_rechecks_owner_at_each_await(runtime, monkeypatch, stage, change):
    after = {}

    def change_owner():
        if change == "delete":
            state_module.remove_identity(1001)
        elif change == "replace":
            state_module._meta_state["identity_states"][1001] = copy.deepcopy(runtime.identity)
        elif change == "rebind":
            state_module.set_identity_account(1001, 22)
        elif change == "disable":
            state_module.set_identity_enabled(1001, False)
        else:
            monkeypatch.setattr(cave_treasure_runtime, "_public_entry_allowed", lambda: False)
        after.update(copy.deepcopy(state_module._meta_state))

    async def completed(*_args, **_kwargs):
        change_owner()
        return runtime.session.return_value if stage == "session" else runtime.flow.return_value

    (runtime.session if stage == "session" else runtime.flow).side_effect = completed
    response = read()
    assert not response["ok"]
    assert response["extra"]["status"] == "cancelled"
    assert state_module._meta_state == after
    runtime.save.assert_not_called()
    runtime.audit.assert_not_awaited()
    if stage == "session":
        runtime.flow.assert_not_awaited()


@pytest.mark.parametrize("command", [".我的阴罗幡", ".我的侍妾"])
@pytest.mark.parametrize("stage", ["session", "result"])
def test_read_only_cannot_overwrite_new_business_state(runtime, command, stage):
    after = {}
    if command == ".我的侍妾":
        runtime.flow.return_value["data"]["actionResult"]["rawMessage"] = CONCUBINE_PANEL

    async def completed(*_args, **_kwargs):
        if command == ".我的阴罗幡":
            runtime.identity["yinluo_observation"]["sha_current"] = 90
        else:
            runtime.identity["concubine_affinity"] = 170
        after.update(copy.deepcopy(runtime.identity))
        return runtime.session.return_value if stage == "session" else runtime.flow.return_value

    (runtime.session if stage == "session" else runtime.flow).side_effect = completed
    assert not read(command)["ok"]
    assert runtime.identity == after
    runtime.save.assert_not_called()
    if stage == "session":
        runtime.flow.assert_not_awaited()


def test_read_only_threads_operation_check_into_both_flows(runtime):
    assert read()["ok"]
    session_check = runtime.session.await_args.kwargs["operation_check"]
    flow_check = runtime.flow.await_args.kwargs["operation_check"]
    assert session_check is flow_check
    state_module.set_identity_account(1001, 22)
    assert not flow_check()


@pytest.mark.parametrize("command", [".化功为煞 10000", ".每日献祭", ".安抚幡灵 1", ".囚禁魂魄 1 凶兽戾魄"])
def test_banner_cannot_erase_an_unanswered_command(runtime, command):
    runtime.identity["pending_tasks"] = {(-10011, 50): {"cmd": command, "time": NOW - 10}}
    before = copy.deepcopy(runtime.identity)
    assert not read()["ok"]
    assert runtime.identity == before
    runtime.save.assert_not_called()


def test_unrelated_pending_command_does_not_block_banner_read(runtime):
    pending = {(-10011, 50): {"cmd": ".点卯", "family": "checkin", "time": NOW - 10}}
    runtime.identity["pending_tasks"] = copy.deepcopy(pending)
    assert read()["ok"]
    assert runtime.identity["pending_tasks"] == pending


def test_explicitly_rejected_command_response_is_not_a_snapshot(runtime):
    runtime.flow.return_value["data"]["actionResult"]["ok"] = False
    before = copy.deepcopy(runtime.identity)
    assert not read()["ok"]
    assert runtime.identity == before
    runtime.save.assert_not_called()


def test_expired_entry_observation_cancels_before_read(runtime):
    async def run():
        with cave_treasure_runtime.observe_cave_public_entry(1001, ENTRY) as observation:
            observation.invalidated = True
            return await cave_treasure_runtime.run_cave_public_tianjige_read_only(1001, ENTRY, ".我的阴罗幡", now=NOW)

    assert not asyncio.run(run())["ok"]
    runtime.session.assert_not_awaited()
    runtime.flow.assert_not_awaited()
    runtime.save.assert_not_called()


def test_older_banner_read_keeps_newer_observation(runtime):
    runtime.identity["yinluo_observation"]["last_observed_at"] = NOW + 1
    before = copy.deepcopy(runtime.identity)
    assert not read()["ok"]
    assert runtime.identity == before
    runtime.save.assert_not_called()


def test_banner_preserves_server_over_capacity_sha(runtime):
    runtime.flow.return_value["data"]["actionResult"]["rawMessage"] = PANEL.replace("120 / 500 (24%)", "269465 / 25000 (100%)")
    assert read()["ok"]
    assert runtime.identity["yinluo_observation"]["sha_current"] == 269465


def test_declared_banner_collections_replace_only_reported_sections(runtime):
    runtime.flow.return_value["data"]["actionResult"]["rawMessage"] = PANEL + (
        "\n魂魄储备:\n - 妖兽精魄: 1 缕\n"
        "幡魂谱系:\n - 怨魂 · 摄魂幡: 0 缕\n"
        "当前特性:\n - 斗法拘魂: +0%"
    )
    assert read()["ok"]
    observed = runtime.identity["yinluo_observation"]
    assert observed["soul_stocks"] == {"妖兽精魄": 1}
    assert observed["soul_lineage"] == {"怨魂": 0}
    assert observed["banner_traits"] == {"斗法拘魂": "+0%"}


@pytest.mark.parametrize("sample_id", ["yinluo.banner.basic", "yinluo.banner.sanshaoye_ready"])
def test_recorded_banner_panels_remain_supported(runtime, sample_id):
    message = get_real_message_text(Path(__file__).parent / "fixtures" / "real_message_samples.json", sample_id)
    runtime.flow.return_value["data"]["actionResult"]["rawMessage"] = message
    parsed = yinluo.parse_yinluo_text(message, now=NOW)
    assert read()["ok"]
    observed = runtime.identity["yinluo_observation"]
    for key in ("sha_current", "sha_max", "sha_percent", "soul_stocks", "ready_slot_numbers", "refining_slot_numbers"):
        assert observed[key] == parsed[key]
    assert observed["auto_next_time"] == NOW + 3600
    assert state_module.get_send_as_profile(1001)["xiuwei_current"] == 100000


@pytest.mark.parametrize("suffix", [
    "\n魂魄储备:\n - 妖兽精魄: 1 缕\n - 凶兽戾魄: 数量未知",
    "\n魂魄储备:\n - 妖兽精魄: 1 缕\n - 妖兽精魄: 2 缕",
    "\n幡魂谱系:\n - 怨魂 · 摄魂幡: 0 缕\n - 修士残魂 · 噬灵幡: 数量未知",
    "\n当前特性:\n - 斗法拘魂: +0%\n - 召魔镇压: 增益未知",
])
def test_partially_parsed_collection_is_not_a_complete_snapshot(runtime, suffix):
    before = copy.deepcopy(runtime.identity)
    runtime.flow.return_value["data"]["actionResult"]["rawMessage"] = PANEL + suffix
    assert not read()["ok"]
    assert runtime.identity == before
    runtime.save.assert_not_called()


def test_unrecognized_compound_slot_status_cannot_become_idle(runtime):
    before = copy.deepcopy(runtime.identity)
    runtime.flow.return_value["data"]["actionResult"]["rawMessage"] = PANEL.replace("[空闲]", "[空闲·待确认]")
    assert not read()["ok"]
    assert runtime.identity == before


def test_read_only_keeps_channel_freeze_and_maintenance_exception(runtime, monkeypatch):
    state_module.set_identity_enabled(1001, False)
    state_module._meta_state["channel_send_as_health"] = {"status": "closed", "restore_identity_ids": [1001]}
    monkeypatch.setattr(cave_treasure_runtime, "get_global_enabled", lambda: False)
    monkeypatch.setattr(cave_treasure_runtime, "get_global_pause_source", lambda: "tianzun_maintenance")
    monkeypatch.setattr(cave_treasure_runtime, "_public_entry_allowed", lambda: (
        cave_treasure_runtime.get_global_enabled()
        or cave_treasure_runtime.get_global_pause_source() == "tianzun_maintenance"
    ))
    assert read()["ok"]


def test_tianjige_flow_aborts_before_auth_when_operation_invalid(runtime, monkeypatch):
    auth = AsyncMock(return_value="fixture_init")
    execute = Mock()
    monkeypatch.setattr(cave_treasure_miniapp, "request_cave_treasure_miniapp_init_data", auth)
    monkeypatch.setattr(cave_treasure_miniapp, "execute_miniapp_http_request", execute)
    result = asyncio.run(cave_treasure_miniapp.run_cave_tianjige_command_production_flow(
        1001, token="df_FIXTURE999", webview_url=ENTRY, command=".我的阴罗幡",
        operation_check=lambda: False,
    ))
    assert not result["ok"]
    auth.assert_not_awaited()
    execute.assert_not_called()


def test_tianjige_flow_rechecks_after_auth(runtime, monkeypatch):
    allowed = True

    async def auth(*_args, **_kwargs):
        nonlocal allowed
        allowed = False
        return "fixture_init"

    execute = Mock()
    monkeypatch.setattr(cave_treasure_miniapp, "request_cave_treasure_miniapp_init_data", auth)
    monkeypatch.setattr(cave_treasure_miniapp, "execute_miniapp_http_request", execute)
    result = asyncio.run(cave_treasure_miniapp.run_cave_tianjige_command_production_flow(
        1001, token="df_FIXTURE999", webview_url=ENTRY, command=".我的阴罗幡",
        operation_check=lambda: allowed,
    ))
    assert not result["ok"]
    execute.assert_not_called()


def test_cancelled_read_holds_entry_lock_until_http_thread_drained(runtime, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    checks = []

    def execute(_request, _transport, **kwargs):
        checks.append(kwargs.get("operation_check"))
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(ok=True, data={"actionResult": {"rawMessage": PANEL}})

    monkeypatch.setattr(cave_treasure_runtime, "run_cave_tianjige_command_production_flow",
                        cave_treasure_miniapp.run_cave_tianjige_command_production_flow)
    monkeypatch.setattr(cave_treasure_miniapp, "execute_miniapp_http_request", execute)

    async def run():
        task = asyncio.create_task(cave_treasure_runtime.run_cave_public_tianjige_read_only(
            1001, ENTRY, ".我的阴罗幡", now=NOW,
        ))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert cave_treasure_runtime.is_cave_public_entry_busy(1001)
            assert callable(checks[0])
            with pytest.raises(MiniAppRequestAborted):
                require_miniapp_operation(checks[0])
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert not cave_treasure_runtime.is_cave_public_entry_busy(1001)
        runtime.save.assert_not_called()
        runtime.audit.assert_not_awaited()

    asyncio.run(run())
