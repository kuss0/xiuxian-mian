import asyncio
from copy import deepcopy
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence, state as state_module
from model.features import fishing_gift as gift, fishing_runtime as fishing, storage_bag as bag
import test_fishing_caller_lifecycle as lifecycle

fishing_db = lifecycle.fishing_db
fishing_env = lifecycle.fishing_env
invalidate = lifecycle.invalidate


@pytest.fixture
def env(fishing_db, monkeypatch):
    h = fishing_db
    h.identity.update(fishing_transfer_target_id=h.other_id, fishing_caught_fish_json='{"fish":2}',
                      fishing_transfer_due_at=h.now - 1)
    for name in ("_storage_bag_transfer_state", "_storage_bag_transfer_batch_state"):
        monkeypatch.setattr(bag, name, deepcopy(getattr(bag, name)))
    monkeypatch.setattr(bag, "_storage_bag_fishing_handoff", None)
    bag._clear_storage_bag_transfer_state()
    bag._clear_storage_bag_transfer_batch_state()
    h.send = AsyncMock(return_value=SimpleNamespace(id=501))
    monkeypatch.setattr(bag, "send_game_command", h.send)
    monkeypatch.setattr(bag, "send_audit_log", h.audit)
    monkeypatch.setattr(bag, "_record_storage_transfer_event", Mock())
    monkeypatch.setattr(bag, "_delete_storage_bag_gift_locator", AsyncMock())
    monkeypatch.setattr(bag, "_maybe_advance_storage_bag_transfer_batch", AsyncMock())
    monkeypatch.setattr(bag, "get_sent_message_chat_id", lambda *a, **k: -10077)
    monkeypatch.setattr(bag, "is_game_send_definitely_unsent", lambda *a: False)
    monkeypatch.setattr(bag, "get_game_group_id", lambda: -10077)
    monkeypatch.setattr(gift, "get_game_group_ids", lambda: [-10077])
    monkeypatch.setattr(bag, "find_recent_storage_bag_gift_anchor", lambda _: {"msg_id": 401, "chat_id": -10077, "text": "anchor"})
    monkeypatch.setattr(bag, "find_message_log_replies", Mock(return_value=[]))
    monkeypatch.setattr(bag, "get_last_game_send_block", lambda *a: {})
    yield h


def persisted(h):
    with sqlite3.connect(persistence.DB_FILE) as conn:
        row = conn.execute("SELECT fishing_gift_handoff, fishing_caught_fish_json FROM identity_runtime_state WHERE send_as_id=?",
                           (h.identity_id,)).fetchone()
    return json.loads(row[0]), row[1]


async def tick(h):
    with state_module.use_identity(h.identity_id):
        return await fishing._run_pending_fishing_transfer(h.now)


def restart(h):
    bag._clear_storage_bag_transfer_state()
    bag._clear_storage_bag_transfer_batch_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    h.identity = state_module.get_identity_state(h.identity_id)


async def receipt(h, item="fish", count=2, msg_id=501, chat_id=-10077):
    return await bag.handle_storage_bag_transfer_reply(
        f"【赠送成功】\n道友 @source 向 @target 赠送了 【{item}】x{count}", h.now,
        reply_to=SimpleNamespace(id=msg_id, raw_text=f".赠送 {item}*{count}", chat_id=chat_id), matched_family="storage_bag_gift")


def test_real_sqlite_queued_behind_another_transfer_survives_restart(env):
    h = env

    async def scenario():
        bag._storage_bag_transfer_state["running"] = True
        assert await tick(h)
        record, queue = persisted(h)
        assert record["phase"] == "queued" and queue == ""
        assert await tick(h)
        h.send.assert_not_awaited()
        restart(h)
        assert h.identity[gift.STATE_KEY] == record
        assert await tick(h)
        h.send.assert_awaited_once()
        assert persisted(h)[0]["phase"] == "waiting"
        assert persisted(h)[0]["msg_id"] == 501
        assert await tick(h)
        h.send.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["active", "sending", "waiting", "held"])
def test_restart_never_retries_interrupted_work(env, phase):
    h = env

    async def scenario():
        await tick(h)
        store = gift.FishingGiftHandoff(h.identity_id, h.other_id)
        store.transition(phase)
        original = persisted(h)[0]
        restart(h)
        await tick(h)
        await tick(h)
        restored = persisted(h)[0]
        assert restored["phase"] == "held"
        assert restored["id"] == original["id"] and restored["items"] == original["items"]
        h.send.assert_not_awaited()
        assert sum("赠送中断" in call.args[0] for call in h.audit.await_args_list) <= 1

    asyncio.run(scenario())


def test_prepare_failure_keeps_original_queue_and_never_sends(env, monkeypatch):
    h = env
    monkeypatch.setattr(persistence, "save_state", lambda: False)
    asyncio.run(tick(h))
    assert json.loads(h.identity["fishing_caught_fish_json"]) == {"fish": 2}
    assert h.identity[gift.STATE_KEY] == {}
    h.send.assert_not_awaited()


def test_intent_save_failure_stops_before_transport(env, monkeypatch):
    h = env

    async def scenario():
        await tick(h)
        original_save = persistence.save_state
        def save():
            return False if h.identity[gift.STATE_KEY]["phase"] == "sending" else original_save()
        monkeypatch.setattr(persistence, "save_state", save)
        await tick(h)
        h.send.assert_not_awaited()
        assert persisted(h)[0]["phase"] == "active"
        assert not bag._storage_bag_transfer_state["running"]

    asyncio.run(scenario())


@pytest.mark.parametrize("outcome", ["none", "timeout", "exception", "cancelled"])
def test_unknown_send_does_not_retry(env, monkeypatch, outcome):
    h = env
    h.send.return_value = None
    if outcome == "timeout":
        monkeypatch.setattr(bag, "get_last_game_send_block", lambda *a: {"code": "send_timeout"})
    if outcome in {"exception", "cancelled"}:
        h.send.side_effect = RuntimeError("network") if outcome == "exception" else asyncio.CancelledError()

    async def scenario():
        await tick(h)
        try:
            await tick(h)
        except (RuntimeError, asyncio.CancelledError):
            pass
        await bag.run_storage_bag_transfer_scheduler(h.now + 100)
        await tick(h)
        h.send.assert_awaited_once()
        assert persisted(h)[0]["phase"] == "held"

    asyncio.run(scenario())


def test_confirmed_send_waits_for_reply_without_resending(env):
    h = env

    async def scenario():
        await tick(h)
        await tick(h)
        await bag.run_storage_bag_transfer_scheduler(h.now + 100)
        await tick(h)
        h.send.assert_awaited_once()
        assert persisted(h)[0]["phase"] == "held"

    asyncio.run(scenario())


def test_proven_unsent_can_retry_via_existing_sender(env, monkeypatch):
    h = env
    h.send.side_effect = [None, SimpleNamespace(id=501)]
    monkeypatch.setattr(bag, "get_last_game_send_block", lambda *a: {"code": "send_queue_timeout"})
    monkeypatch.setattr(bag, "is_game_send_definitely_unsent", lambda *a: True)

    async def scenario():
        await tick(h)
        await tick(h)
        assert persisted(h)[0]["phase"] == "active"
        await bag.run_storage_bag_transfer_scheduler(h.now + 6)
        assert h.send.await_count == 2 and persisted(h)[0]["phase"] == "waiting"
        assert await receipt(h)
        assert persisted(h)[0]["phase"] == "done"

    asyncio.run(scenario())


def test_partial_success_is_atomic_and_restart_does_not_repeat_first_item(env):
    h = env
    h.identity["fishing_caught_fish_json"] = '{"fish":2,"other":1}'
    state_module.set_storage_bag_records({str(h.identity_id): {"items": {"fish": 2, "other": 1}},
                                          str(h.other_id): {"items": {}}})

    async def scenario():
        await tick(h)
        await tick(h)
        assert await receipt(h)
        record = persisted(h)[0]
        assert record["confirmed"] == 1 and record["phase"] == "active"
        assert state_module.get_storage_bag_records()[str(h.other_id)]["items"]["fish"] == 2
        assert not await receipt(h)
        restart(h)
        await tick(h)
        assert persisted(h)[0]["phase"] == "held" and persisted(h)[0]["confirmed"] == 1
        assert state_module.get_storage_bag_records()[str(h.other_id)]["items"]["fish"] == 2
        h.send.assert_awaited_once()

    asyncio.run(scenario())


def test_receipt_save_failure_rolls_back_inventory_and_blocks_next_item(env, monkeypatch):
    h = env

    async def scenario():
        await tick(h)
        await tick(h)
        before = deepcopy(state_module.get_storage_bag_records())
        monkeypatch.setattr(persistence, "save_state", lambda: False)
        assert await receipt(h)
        assert state_module.get_storage_bag_records() == before
        assert persisted(h)[0]["phase"] == "waiting"
        assert persisted(h)[0]["confirmed"] == 0
        h.send.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "module_disabled", "target"])
def test_owner_or_config_change_during_send_cannot_advance(env, change):
    h = env

    async def send(*a, **k):
        if change == "target":
            state_module.set_identity_account(h.other_id, 7199)
        else:
            invalidate(h, change)
        return SimpleNamespace(id=501)
    h.send.side_effect = send

    async def scenario():
        await tick(h)
        await tick(h)
        if change == "module_disabled":
            # A confirmed receipt may still be accounted after the module is disabled.
            assert await receipt(h)
            assert persisted(h)[0]["phase"] == "done"
        else:
            assert not bag._storage_bag_transfer_state["running"]
        h.send.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("bad", [None, [], {"invalid": True}, {"version": 1, "phase": []}])
def test_corrupt_handoff_never_clears_queue_or_sends(env, bad):
    h = env
    h.identity[gift.STATE_KEY] = bad
    asyncio.run(tick(h))
    assert h.identity["fishing_caught_fish_json"] == '{"fish":2}'
    assert h.identity[gift.STATE_KEY] == bad
    h.send.assert_not_awaited()


def test_next_catch_is_separate_from_inflight_handoff(env):
    h = env

    async def scenario():
        await tick(h)
        first = persisted(h)[0]["id"]
        h.identity["fishing_caught_fish_json"] = '{"fish":3}'
        await tick(h)
        assert await receipt(h)
        assert json.loads(h.identity["fishing_caught_fish_json"]) == {"fish": 3}
        await tick(h)
        record = persisted(h)[0]
        assert record["id"] != first and record["items"][0]["quantity"] == 3
        h.send.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("chat_id,msg_id", [(0, 501), (-10078, 501), (-10077, 999)])
def test_foreign_or_unscoped_reply_cannot_confirm(env, chat_id, msg_id):
    h = env

    async def scenario():
        await tick(h)
        await tick(h)
        assert not await receipt(h, chat_id=chat_id, msg_id=msg_id)
        assert persisted(h)[0]["phase"] == "waiting" and persisted(h)[0]["confirmed"] == 0
        assert await receipt(h)
        assert persisted(h)[0]["phase"] == "done"

    asyncio.run(scenario())


def test_successful_multi_item_transfer_obeys_interval_and_completes(env):
    h = env
    h.identity["fishing_caught_fish_json"] = '{"fish":2,"other":1}'
    h.send.side_effect = [SimpleNamespace(id=501), SimpleNamespace(id=502)]

    async def scenario():
        await tick(h)
        await tick(h)
        assert await receipt(h)
        await bag.run_storage_bag_transfer_scheduler(h.now + 19)
        h.send.assert_awaited_once()
        await bag.run_storage_bag_transfer_scheduler(h.now + 21)
        assert h.send.await_count == 2
        assert await receipt(h, item="other", count=1, msg_id=502)
        assert persisted(h)[0]["phase"] == "done"
        restart(h)
        assert not await tick(h)
        assert h.send.await_count == 2

    asyncio.run(scenario())


def test_original_bound_reply_recovery_does_not_send_again(env, monkeypatch):
    h = env
    state_module._meta_state["game_bot_ids"] = [9001]
    entry = dict(event_type="edit", sender_id=9001, sender_is_bot=True, chat_id=-10077, reply_to_msg_id=501,
                 message_id=601, text="【赠送成功】\n道友 @source 向 @target 赠送了 【fish】x2")

    def find(msg_id, now, **kwargs):
        assert msg_id == 501 and kwargs["chat_id"] == -10077
        predicate = kwargs["predicate"]
        assert predicate(entry)
        assert not predicate({**entry, "sender_id": 9002})
        assert not predicate({**entry, "sender_is_bot": False})
        return [entry]

    monkeypatch.setattr(bag, "find_message_log_replies", find)

    async def scenario():
        await tick(h)
        await tick(h)
        await bag.run_storage_bag_transfer_scheduler(h.now + 100)
        assert persisted(h)[0]["phase"] == "done"
        h.send.assert_awaited_once()

    asyncio.run(scenario())


def test_replaced_record_during_rpc_cannot_be_overwritten(env):
    h = env

    async def send(*a, **k):
        h.identity[gift.STATE_KEY] = {**h.identity[gift.STATE_KEY], "id": "f" * 32}
        return SimpleNamespace(id=501)

    h.send.side_effect = send

    async def scenario():
        await tick(h)
        await tick(h)
        assert h.identity[gift.STATE_KEY]["id"] == "f" * 32
        assert h.identity[gift.STATE_KEY]["phase"] == "sending"
        assert not bag._storage_bag_transfer_state["running"]
        h.send.assert_awaited_once()

    asyncio.run(scenario())


@pytest.mark.parametrize("key", ["fishing_operation", "fishing_result_pending", "fishing_native_operation", "fishing_native_supply"])
def test_unresolved_fishing_receipt_blocks_handoff(env, key):
    h = env
    h.identity[key] = {"invalid": True}
    assert not asyncio.run(tick(h))
    assert h.identity[gift.STATE_KEY] == {}
    assert h.identity["fishing_caught_fish_json"] == '{"fish":2}'
    h.send.assert_not_awaited()


def test_oversized_or_invalid_persisted_record_is_preserved_as_invalid(env):
    h = env
    h.identity[gift.STATE_KEY] = {"blob": "x" * (gift.MAX_BYTES + 1)}
    assert persistence.save_state()
    assert persisted(h)[0] == {"invalid": True}
    restart(h)
    asyncio.run(tick(h))
    assert h.identity[gift.STATE_KEY] == {"invalid": True}
    h.send.assert_not_awaited()


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound", "module_disabled", "target", "target_disabled", "group"])
def test_dispatch_check_revalidates_while_queued(env, monkeypatch, change):
    h = env

    async def send(*a, **kwargs):
        assert kwargs["target_chat_id"] == -10077
        assert kwargs["operation_check"]() is True
        if change == "target":
            state_module.set_identity_account(h.other_id, 7199)
        elif change == "target_disabled":
            state_module.set_identity_enabled(h.other_id, False)
        elif change == "group":
            monkeypatch.setattr(gift, "get_game_group_ids", lambda: [-10078])
        else:
            invalidate(h, change)
        assert kwargs["operation_check"]() is False
        return None

    h.send.side_effect = send

    async def scenario():
        await tick(h)
        await tick(h)
        h.send.assert_awaited_once()
        assert not bag._storage_bag_transfer_state["running"]

    asyncio.run(scenario())


@pytest.mark.parametrize("group", [0, -10099])
def test_missing_or_unconfigured_anchor_group_never_sends(env, monkeypatch, group):
    h = env
    monkeypatch.setattr(bag, "find_recent_storage_bag_gift_anchor", lambda _: {"msg_id": 401, "chat_id": group, "text": "anchor"})

    async def scenario():
        await tick(h)
        await tick(h)
        h.send.assert_not_awaited()
        assert persisted(h)[0]["phase"] == "held"

    asyncio.run(scenario())


def test_locator_and_gift_share_explicit_group_and_transport_check(env, monkeypatch):
    h = env
    monkeypatch.setattr(bag, "find_recent_storage_bag_gift_anchor", lambda _: {})
    h.send.side_effect = [SimpleNamespace(id=401), SimpleNamespace(id=501)]

    async def scenario():
        await tick(h)
        await tick(h)
        assert h.send.await_count == 2
        for call in h.send.await_args_list:
            assert call.kwargs["target_chat_id"] == -10077
            assert callable(call.kwargs["operation_check"])
        assert h.send.await_args.kwargs["reply_to"] == 401
        assert await receipt(h)
        assert persisted(h)[0]["phase"] == "done"

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["removed", "replaced", "rebound"])
def test_old_caller_cannot_write_error_into_replacement_identity(env, change):
    h = env
    expected = {}

    async def send(*a, **kwargs):
        invalidate(h, change)
        expected.update(deepcopy(state_module._meta_state["identity_states"]))
        return SimpleNamespace(id=501)

    h.send.side_effect = send

    async def scenario():
        await tick(h)
        await tick(h)
        assert state_module._meta_state["identity_states"] == expected
        h.send.assert_awaited_once()

    asyncio.run(scenario())
