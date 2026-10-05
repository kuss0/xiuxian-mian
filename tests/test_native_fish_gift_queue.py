import asyncio
from copy import deepcopy
import json

import pytest

from model import persistence, state as state_module
from model.features import fishing_dwelling_store as native, fishing_gift as gift, storage_bag as bag
from model.features.fishing_dwelling_protocol import ProtocolError
import test_fishing_gift_handoff as handoff
from test_fishing_dwelling_store import settled_store

env = handoff.env
fishing_db = handoff.fishing_db
fishing_env = handoff.fishing_env


@pytest.mark.parametrize("due", [0, 1700000200])
def test_native_queue_and_inventory_commit_once_and_survive_restart(env, due):
    h = env
    h.identity.update(fishing_caught_fish_json='{"fish":3,"older":1}', fishing_transfer_due_at=due)

    async def scenario():
        store = settled_store(h)
        assert store.project(h.now)
        assert not store.project(h.now)
        handoff.restart(h)
        assert json.loads(h.identity["fishing_caught_fish_json"]) == {"fish": 5, "older": 1}
        assert h.identity["fishing_transfer_due_at"] == (
            due or h.now + handoff.fishing.fishing_behavior.FISHING_TRANSFER_QUEUE_DELAY_SEC)
        assert h.identity[native.STATE_KEY]["phase"] == "accounted"
        assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"]["fish"] == 2
        h.send.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["disabled", "public_only", "canary", "target", "queue", "timer", "no_target"])
def test_native_queue_does_not_expand_authority_or_overwrite_new_plan(env, monkeypatch, change):
    h = env
    if change == "no_target":
        h.identity["fishing_transfer_target_id"] = 0

    async def scenario():
        if change == "public_only":
            h.identity["fishing_enabled"] = False
            monkeypatch.setattr(handoff.fishing, "is_cave_public_auto_enabled", lambda *args: True)
        store = settled_store(h)
        updates = {
            "disabled": {"fishing_enabled": False}, "target": {"fishing_transfer_target_id": 771122},
            "queue": {"fishing_caught_fish_json": '{"manual":4}'}, "timer": {"fishing_transfer_due_at": h.now + 500},
        }
        h.identity.update(updates.get(change, {}))
        before = {key: h.identity[key] for key in ("fishing_caught_fish_json", "fishing_transfer_due_at", "fishing_transfer_target_id")}
        assert store.project(h.now, update_schedule=change != "canary")
        assert {key: h.identity[key] for key in before} == before
        assert h.identity[native.STATE_KEY]["phase"] == "accounted"
        h.send.assert_not_awaited()

    asyncio.run(scenario())


def test_queue_rolls_back_with_inventory_on_save_failure(env, monkeypatch):
    h = env

    async def scenario():
        store = settled_store(h)
        before = deepcopy(h.identity)
        inventory = deepcopy(state_module.get_storage_bag_records())
        monkeypatch.setattr(persistence, "save_state", lambda: False)
        with pytest.raises(ProtocolError, match="projection_save_failed"):
            store.project(h.now)
        assert h.identity == before
        assert state_module.get_storage_bag_records() == inventory

    asyncio.run(scenario())


@pytest.mark.parametrize("queue", ['broken', '[]', '{"fish":true}', '{"fish":-1}', '{"fish":1000000000}'])
def test_corrupt_or_overflowing_queue_is_preserved_for_review(env, queue):
    h = env
    h.identity["fishing_caught_fish_json"] = queue

    async def scenario():
        store = settled_store(h)
        before = deepcopy(h.identity)
        inventory = deepcopy(state_module.get_storage_bag_records())
        with pytest.raises(ProtocolError, match="native_transfer"):
            store.project(h.now)
        assert h.identity == before
        assert state_module.get_storage_bag_records() == inventory

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["confirmed", "unknown_restart"])
def test_native_settlement_to_real_handoff_waits_survives_restart_and_confirms_once(env, outcome):
    h = env
    h.identity.update(fishing_caught_fish_json="", fishing_transfer_due_at=0)

    async def scenario():
        store = settled_store(h)
        assert store.project(h.now)
        assert not store.project(h.now)
        h.now = h.identity["fishing_transfer_due_at"]
        bag._storage_bag_transfer_state["running"] = True
        assert await handoff.tick(h)
        assert await handoff.tick(h)
        h.send.assert_not_awaited()
        queued = deepcopy(h.identity[gift.STATE_KEY])
        assert queued["phase"] == "queued"
        assert h.identity["fishing_caught_fish_json"] == ""
        handoff.restart(h)
        assert h.identity[gift.STATE_KEY] == queued
        assert await handoff.tick(h)
        h.send.assert_awaited_once()
        assert h.identity[gift.STATE_KEY]["phase"] == "waiting"
        if outcome == "unknown_restart":
            handoff.restart(h)
            assert await handoff.tick(h)
            assert await handoff.tick(h)
            assert h.identity[gift.STATE_KEY]["phase"] == "held"
            h.send.assert_awaited_once()
            return
        assert await handoff.receipt(h)
        assert h.identity[gift.STATE_KEY]["phase"] == "done"
        assert not await handoff.tick(h)
        assert not native.NativeFishingStore(h.identity_id, -100991060001).project(h.now)
        h.send.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("caught", [False, True])
def test_only_caught_fish_enter_queue_not_bonus_materials_or_bait(env, caught):
    h = env
    h.identity["fishing_caught_fish_json"] = '{"older":1}'

    async def scenario():
        store = settled_store(h, caught=caught, bonus=[{"name": "gem", "qty": 3}])
        assert store.project(h.now)
        assert json.loads(h.identity["fishing_caught_fish_json"]) == (
            {"older": 1, "fish": 2} if caught else {"older": 1})
        assert state_module.get_storage_bag_records()[str(h.identity_id)]["items"] == (
            {"fish": 2, "gem": 3, "bait": 5} if caught else {"gem": 3, "bait": 5})

    asyncio.run(scenario())
