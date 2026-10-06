import asyncio
from copy import deepcopy

import pytest

from model import state as state_module
from model.features import cave_treasure_runtime as cave
from model.features import cave_treasure_miniapp as dwelling
from model.features.miniapp_common import MiniAppFlowCancelled
import test_cave_small_world_lifecycle as lifecycle
from test_cave_small_world_lifecycle import ENTRY_URL, IDENTITY_ID, NOW, flow_result


world_env = lifecycle.world_env


MESSAGE = "你大手一挥，将凡间供奉的 **6031** 点香火尽数收入紫府。\n当前香火库存: 236283"


def result():
    value = flow_result()
    value["data"]["snapshot_current"] = False
    value["data"]["action_result"] = {"ok": True, "completed": True, "rawMessage": MESSAGE, "message": MESSAGE}
    return value


@pytest.mark.parametrize("message", [MESSAGE, MESSAGE.replace("**", ""), MESSAGE.replace("\n", "\r\n")])
def test_exact_receipt_keeps_actual_stock_not_added_to_stale_balance(message):
    value = result()
    value["data"]["action_result"] = {"ok": True, "completed": True, "message": message}
    assert cave._partial_cave_harvest_receipt(value, IDENTITY_ID) == {"collected": 6031, "stock": 236283}


@pytest.mark.parametrize("change", [
    {"ok": False}, {"ok": 1}, {"error": "warning"}, {"status": "cancelled"}, {"retry_after_sec": 60},
    {"outcome_unknown": True},
])
def test_unconfirmed_or_rate_limited_envelope_is_not_receipt(change):
    value = {**result(), **change}
    assert cave._partial_cave_harvest_receipt(value, IDENTITY_ID) is None


@pytest.mark.parametrize("key,value", [
    ("action", "manifest"), ("action_confirmed", False), ("action_dispatched", False),
    ("snapshot_current", True), ("raw", {}),
    ("outcome_unknown", True),
])
def test_wrong_action_or_missing_identity_never_supplies_stock(key, value):
    payload = result()
    payload["data"][key] = value
    assert cave._partial_cave_harvest_receipt(payload, IDENTITY_ID) is None


@pytest.mark.parametrize("message", [
    MESSAGE + "\n当前香火库存: 1", MESSAGE.replace("6031", "6031.5"), MESSAGE.replace("6031", "-6031"),
    MESSAGE.replace("6031", "6,031"), MESSAGE.replace("6031", "60*31"),
    MESSAGE.replace("236283", "6000"), MESSAGE.replace("236283", str(2**53)), "收割成功",
    MESSAGE.replace("**6031**", "**6031"), "示例：" + MESSAGE,
])
def test_ambiguous_or_malformed_text_is_not_a_balance(message):
    payload = result()
    payload["data"]["action_result"] = {"ok": True, "completed": True, "rawMessage": message}
    assert cave._partial_cave_harvest_receipt(payload, IDENTITY_ID) is None


def test_conflicting_message_and_raw_message_are_not_accepted():
    payload = result()
    payload["data"]["action_result"]["message"] = MESSAGE.replace("236283", "236284")
    assert cave._partial_cave_harvest_receipt(payload, IDENTITY_ID) is None


def test_known_text_from_another_game_identity_does_not_supply_stock():
    payload = result()
    payload["data"]["raw"]["account"]["playerId"] = IDENTITY_ID + 1
    assert cave._partial_cave_harvest_receipt(payload, IDENTITY_ID) is None


def test_business_error_cannot_be_hidden_behind_success_flags():
    payload = result()
    payload["data"]["action_result"]["error"] = "conflicting-result"
    assert cave._partial_cave_harvest_receipt(payload, IDENTITY_ID) is None


@pytest.mark.parametrize("completed", [False, None, 1, "true"])
def test_completion_is_explicit(completed):
    payload = result()
    payload["data"]["action_result"]["completed"] = completed
    assert cave._partial_cave_harvest_receipt(payload, IDENTITY_ID) is None


def test_owned_partial_harvest_updates_only_stock_and_retains_stale_panel(world_env):
    h = world_env
    before = deepcopy(h.identity)
    h.flow.return_value = result()
    response = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW, harvest_only=True))
    assert response["ok"]
    assert h.identity["small_world_incense_stock"] == 236283
    assert h.identity["small_world_panel_snapshot"] == {**before["small_world_panel_snapshot"], "stock": 236283, "updated_at": 0}
    assert h.identity["small_world_last_panel_at"] == 0
    assert h.identity["small_world_pending_incense"] == before["small_world_pending_incense"]
    assert h.identity["small_world_faith_value"] == before["small_world_faith_value"]
    assert h.identity["small_world_refine_enabled"] == before["small_world_refine_enabled"]
    assert h.identity["next_small_world_time"] == before["next_small_world_time"]
    assert h.identity["small_world_next_public_harvest_at"] == NOW + 28800
    saved = state_module.get_miniapp_state_records()[f"{IDENTITY_ID}:cave_small_world"]["state"]
    assert saved["snapshot_current"] is False
    assert saved["last_harvest_receipt"] == {"collected": 6031, "stock": 236283, "observed_at": NOW}
    assert saved["incense_stock"] == 236283
    h.flow.assert_awaited_once()
    assert h.audit.await_args.kwargs["priority"] == "low"
    again = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW + 30, harvest_only=True))
    assert again["extra"]["skipped"]
    h.flow.assert_awaited_once()


def test_late_receipt_cannot_overwrite_newer_panel(world_env):
    h = world_env

    async def flow(*_args, **_kwargs):
        h.identity.update(small_world_incense_stock=300000, small_world_last_panel_at=NOW + 10,
                          small_world_panel_snapshot={"stock": 300000, "faith": 80, "updated_at": NOW + 10})
        return result()

    h.flow.side_effect = flow
    asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW, harvest_only=True))
    assert h.identity["small_world_incense_stock"] == 300000
    assert h.identity["small_world_panel_snapshot"]["updated_at"] == NOW + 10
    assert "last_harvest_receipt" not in state_module.get_miniapp_state_records()[f"{IDENTITY_ID}:cave_small_world"]["state"]


@pytest.mark.parametrize("change", ["account", "identity", "removed"])
def test_receipt_after_owner_change_does_not_update_replacement(world_env, change):
    h = world_env

    async def flow(*_args, **_kwargs):
        if change == "removed":
            state_module.remove_identity(IDENTITY_ID)
        elif change == "identity":
            state_module.remove_identity(IDENTITY_ID)
            state_module.set_identity_account(IDENTITY_ID, 7401)
        else:
            state_module.set_identity_account(IDENTITY_ID, 7402)
        return result()

    h.flow.side_effect = flow
    response = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW, harvest_only=True))
    assert response["ok"] is False
    if change == "removed":
        assert not state_module.has_identity(IDENTITY_ID)
    else:
        assert state_module.get_identity_state(IDENTITY_ID)["small_world_incense_stock"] != 236283
    assert state_module.get_miniapp_state_records() == {}
    h.audit.assert_not_awaited()


def test_newer_record_blocks_all_partial_stock_updates(world_env):
    h = world_env
    before = deepcopy(h.identity)
    newer = {"updated_at": NOW + 1, "state": {"incense_stock": 300000, "snapshot_current": True}}

    async def flow(*_args, **_kwargs):
        state_module.set_miniapp_state_records({f"{IDENTITY_ID}:cave_small_world": deepcopy(newer)})
        return result()

    h.flow.side_effect = flow
    asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW, harvest_only=True))
    assert h.identity["small_world_incense_stock"] == before["small_world_incense_stock"]
    assert h.identity["small_world_panel_snapshot"] == before["small_world_panel_snapshot"]
    assert state_module.get_miniapp_state_records()[f"{IDENTITY_ID}:cave_small_world"] == newer


def test_receipt_does_not_invent_a_missing_full_panel(world_env):
    h = world_env
    h.identity["small_world_panel_snapshot"] = {}
    h.flow.return_value = result()
    asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW, harvest_only=True))
    assert h.identity["small_world_incense_stock"] == 236283
    assert h.identity["small_world_panel_snapshot"] == {}
    assert h.identity["small_world_last_panel_at"] == 0


def test_disabling_harvest_in_flight_keeps_receipt_without_reenabling(world_env):
    h = world_env
    before = deepcopy(h.identity)

    async def flow(*_args, **_kwargs):
        h.identity["small_world_harvest_enabled"] = False
        return result()

    h.flow.side_effect = flow
    response = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW, harvest_only=True))
    assert response["extra"]["operation_cancelled"]
    assert h.identity["small_world_incense_stock"] == 236283
    assert h.identity["small_world_harvest_enabled"] is False
    assert h.identity["next_small_world_time"] == before["next_small_world_time"]
    h.audit.assert_not_awaited()


@pytest.mark.parametrize("stage", ["action", "notification"])
def test_cancellation_retains_partial_receipt_without_repeating_harvest(world_env, stage):
    h = world_env
    h.flow.return_value = result()
    if stage == "action":
        h.flow.side_effect = MiniAppFlowCancelled(result())
    else:
        h.audit.side_effect = asyncio.CancelledError()
    with pytest.raises(MiniAppFlowCancelled) as raised:
        asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW, harvest_only=True))
    assert raised.value.result["ok"]
    assert h.identity["small_world_incense_stock"] == 236283
    assert h.identity["small_world_next_public_harvest_at"] == NOW + 28800
    saved = state_module.get_miniapp_state_records()[f"{IDENTITY_ID}:cave_small_world"]["state"]
    assert saved["last_harvest_receipt"]["stock"] == 236283
    assert saved["snapshot_current"] is False
    response = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW + 30, harvest_only=True))
    assert response["extra"]["skipped"]
    h.flow.assert_awaited_once()


def test_notification_failure_does_not_lose_partial_receipt(world_env):
    h = world_env
    h.flow.return_value = result()
    h.audit.side_effect = RuntimeError("fixture-delivery-failure")
    response = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW, harvest_only=True))
    assert response["ok"]
    assert h.identity["small_world_incense_stock"] == 236283
    assert h.identity["small_world_next_public_harvest_at"] == NOW + 28800
    h.flow.assert_awaited_once()


def test_real_worker_partial_reply_needs_no_second_read_or_action(world_env, monkeypatch):
    h = world_env
    calls = []

    def transport(request):
        calls.append(request["safe_summary"]["endpoint"])
        return {"ok": True, "account": {"playerId": IDENTITY_ID},
                "actionResult": {"ok": True, "completed": True, "rawMessage": MESSAGE},
                "snapshot": {"partial": True, "domains": ["companion", "sect"]}}

    async def flow(identity_id, **kwargs):
        return await dwelling.run_cave_small_world_production_flow(identity_id, **kwargs, transport=transport)

    monkeypatch.setattr(cave, "run_cave_small_world_production_flow", flow)
    response = asyncio.run(cave.run_cave_public_small_world_sync(IDENTITY_ID, ENTRY_URL, now=NOW, harvest_only=True))
    assert response["ok"] and h.identity["small_world_incense_stock"] == 236283
    assert len(calls) == 1
    assert h.audit.await_args.kwargs["priority"] == "low"
