import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, app_runtime, persistence, runtime, state as state_module
from model.features import concubine, concubine_affinity_actions, passive_inbox


ID, ACCOUNT, CHAT, BOT, ROOT = 99083001, 8301, -100830001, 88083001, 83001
NOW = 1_700_000_500.0
FIELD = "concubine_gift_actions"
STONE = "\u7075\u77f3"
NAME = "\u51cc\u7389\u7075"
BAG = "@gift_owner \u7684\u50a8\u7269\u888b\n\u6750\u6599:\n- \u7075\u77f3 x 1,000\n"
SUCCESS = "\u4f60\u5c06\u3010\u7075\u77f3\u3011x60 \u8d60\u4e88\u4e86\u4f8d\u59be\u3010\u51cc\u7389\u7075\u3011\uff0c\u4f60\u4eec\u7684\u60c5\u7f18\u589e\u52a0\u4e86 60 \u70b9\uff01"


@pytest.fixture
def env(monkeypatch, tmp_path):
    before = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_game_group_id(CHAT)
    state_module.set_game_bot_ids([BOT])
    state_module.set_identity_account(ID, ACCOUNT)
    state_module.update_send_as_profile(ID, username="gift_owner", sect_name="\u661f\u5bab")
    identity = state_module.get_identity_state(ID)
    identity.update(
        concubine_enabled=True, concubine_tianji_enabled=True,
        concubine_phase="idle", concubine_availability="available",
        concubine_name=NAME, concubine_kind="\u9053\u5fc3\u4f8d\u59be", concubine_affinity=240,
        concubine_last_snapshot_at=NOW - 1, concubine_last_panel_msg_id=ROOT - 1,
        concubine_last_greet_day=concubine._local_day_key(NOW),
    )
    state_module.set_storage_bag_records({str(ID): {
        "identity_id": ID, "items": {STONE: 1000}, "sections": {"\u6750\u6599": {STONE: 1000}},
        "updated_at": NOW - 1,
    }})
    clock, block = [NOW], {"status": "none"}
    send = AsyncMock(return_value=SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW + .2, send_started_at=NOW))
    save, audit = Mock(return_value=True), AsyncMock()
    monkeypatch.setattr(concubine, "send_game_command", send)
    monkeypatch.setattr(concubine_affinity_actions, "_INFLIGHT", {})
    monkeypatch.setattr(concubine, "save_state", save)
    monkeypatch.setattr(concubine, "mark_dirty", Mock())
    monkeypatch.setattr(concubine, "send_audit_log", audit)
    monkeypatch.setattr(concubine, "console_log", Mock())
    monkeypatch.setattr(concubine.time, "time", lambda: clock[0])
    monkeypatch.setattr(concubine.random, "uniform", lambda *_args: 120)
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args, **_kwargs: dict(block))
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", Mock(return_value=[]))
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[]))
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "gift.db"))
    monkeypatch.setattr(runtime, "_notify_game_command_sent_observers", Mock())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "action_guard_note_sent", Mock())
    monkeypatch.setattr(app, "_remember_early_routed_reply", Mock())
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(passive_inbox, "save_state", save)
    monkeypatch.setattr(passive_inbox, "_save_passive_stats", Mock())
    monkeypatch.setattr(passive_inbox, "_observed_passive_events", {})
    monkeypatch.setattr(passive_inbox, "_passive_stats", {
        "total": 0, "changed": 0, "skipped": 0, "modules": {}, "skip_reasons": {}, "recent": [],
    })
    try:
        yield SimpleNamespace(identity=identity, clock=clock, send=send, save=save, audit=audit, block=block)
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(before)


async def send_bag():
    with state_module.use_identity(ID):
        return await concubine._send_gift_bag_command(concubine.time.time())


def receipt(env, kind="gift_bag", root=ROOT):
    record = env.identity[FIELD][kind]
    env.identity["pending_tasks"][(CHAT, root)] = {
        "cmd": record["command"], "family": "storage_bag" if kind == "gift_bag" else "concubine_gift",
        "op_id": record["op_id"], "source_module": "concubine_gift",
        "account_id": ACCOUNT, "chat_id": CHAT, "message_id": root,
        "sent_at": env.clock[0] + .2, "send_started_at": env.clock[0],
    }


async def reply(env, kind="gift_bag", *, root=None, text=None, **changes):
    root = root or (ROOT if kind == "gift_bag" else ROOT + 10)
    command = concubine.CMD_STORAGE_BAG if kind == "gift_bag" else f"{concubine.CMD_CONCUBINE_GIFT_STONE} {STONE}*60"
    kwargs = dict(
        matched_family="storage_bag" if kind == "gift_bag" else "concubine_gift",
        current_msg_id=root + 1, current_chat_id=CHAT, observed_at=env.clock[0] + 1,
        reply_context={"sender_id": BOT},
    )
    kwargs.update(changes)
    handler = concubine.handle_concubine_storage_bag_reply if kind == "gift_bag" else concubine.handle_concubine_gift_reply
    with state_module.use_identity(ID):
        return await handler(
            (BAG if kind == "gift_bag" else SUCCESS) if text is None else text,
            env.clock[0] + 2, SimpleNamespace(id=root, raw_text=command, chat_id=CHAT), **kwargs,
        )


def test_bag_persists_owned_intent_before_dispatch(env):
    async def sent(command, **kwargs):
        record = env.identity.get(FIELD, {}).get("gift_bag", {})
        assert record["status"] == "sending"
        assert record["command"] == command
        assert record["identity_id"] == ID and record["account_id"] == ACCOUNT
        assert kwargs["send_as_id"] == ID and kwargs["target_chat_id"] == CHAT
        assert kwargs["op_id"] == record["op_id"]
        assert kwargs["track"] is True and kwargs["max_retry"] == 0
        assert kwargs["operation_check"]()
        env.save.assert_called()
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(send_bag())
    assert env.identity[FIELD]["gift_bag"]["status"] == "sent"


@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_late_bag_receipt_never_writes_to_a_replacement(env, change):
    after = {}

    async def sent(_command, **_kwargs):
        if change == "delete":
            state_module.remove_identity(ID)
        elif change == "replace":
            state_module._meta_state["identity_states"][ID] = copy.deepcopy(env.identity)
        else:
            state_module.set_identity_account(ID, ACCOUNT + 1)
        after.update(copy.deepcopy(state_module._meta_state))
        return env.send.return_value

    env.send.side_effect = sent
    assert not asyncio.run(send_bag())
    assert state_module._meta_state == after


@pytest.mark.parametrize("mode", ["none", "exception", "cancel"])
def test_unknown_bag_keeps_ownership_and_cannot_restart_immediately(env, mode):
    env.send.return_value = None
    if mode != "none":
        env.send.side_effect = RuntimeError("fixture") if mode == "exception" else asyncio.CancelledError()
        with pytest.raises(RuntimeError if mode == "exception" else asyncio.CancelledError):
            asyncio.run(send_bag())
    else:
        assert not asyncio.run(send_bag())
    assert env.identity[FIELD]["gift_bag"]["status"] == "unknown"
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 1)
    assert env.identity == before
    assert not asyncio.run(send_bag())
    env.send.assert_awaited_once()


@pytest.mark.parametrize("change", ["pause", "disable", "module", "partner", "affinity", "schedule", "chat"])
def test_bag_queue_revalidates_ownership_and_business_plan(env, change):
    async def sent(_command, **kwargs):
        if change == "pause":
            state_module.set_global_enabled(False)
        elif change == "disable":
            state_module.set_identity_enabled(ID, False)
        elif change == "module":
            env.identity["concubine_tianji_enabled"] = False
        elif change == "partner":
            env.identity["concubine_name"] = "replacement"
        elif change == "affinity":
            env.identity["concubine_affinity"] = 300
        elif change == "schedule":
            env.identity["next_concubine_time"] = NOW + 40000
        else:
            state_module.set_game_group_id(CHAT - 1)
        assert not kwargs["operation_check"]()
        env.block.update(status="unsent", code="operation_cancelled", at=NOW)
        return None

    env.send.side_effect = sent
    assert not asyncio.run(send_bag())
    assert env.identity[FIELD]["gift_bag"]["status"] == "unsent"
    if change == "schedule":
        assert env.identity["next_concubine_time"] == NOW + 40000


def test_no_gift_without_an_owned_complete_inventory_read(env):
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine._send_gift_command(NOW, 60))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("phase,key", [("gift_pending", "concubine_gift_msg_id"), ("gift_bag_pending", "concubine_gift_bag_msg_id")])
def test_legacy_gift_pending_is_not_erased_or_retried(env, phase, key):
    env.identity.update(concubine_phase=phase, next_concubine_time=NOW - 1)
    env.identity[key] = ROOT
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW)
        asyncio.run(concubine.run_concubine_scheduler(NOW + 1))
    assert env.identity["concubine_phase"] == phase
    assert env.identity[key] == ROOT
    env.send.assert_not_awaited()


def test_failed_intent_save_prevents_bag_dispatch(env):
    env.save.return_value = False
    assert not asyncio.run(send_bag())
    env.send.assert_not_awaited()


def prepare_gift(env, monkeypatch):
    assert asyncio.run(send_bag())
    receipt(env)
    with monkeypatch.context() as scoped:
        followup = AsyncMock(return_value=True)
        scoped.setattr(concubine, "_send_gift_command", followup)
        assert asyncio.run(reply(env))
        followup.assert_awaited_once_with(NOW + 2, 60)
    env.clock[0] = NOW + 2
    env.send.reset_mock()
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=NOW + 2.2, send_started_at=NOW + 2)
    assert env.identity[FIELD]["gift_bag"]["status"] == "complete"


async def send_gift(env):
    with state_module.use_identity(ID):
        return await concubine._send_gift_command(env.clock[0], 60)


def test_owned_gift_result_applies_exactly_once(env, monkeypatch):
    prepare_gift(env, monkeypatch)
    assert asyncio.run(send_gift(env))
    receipt(env, "gift", ROOT + 10)
    assert asyncio.run(reply(env, "gift"))
    assert env.identity["concubine_affinity"] == 300
    assert env.identity["concubine_last_gift_day"] == concubine._local_day_key(NOW)
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["concubine_gift_amount"] == 0
    assert (CHAT, ROOT + 10) not in env.identity["pending_tasks"]
    assert state_module.get_storage_bag_records()[str(ID)]["items"][STONE] == 940
    before = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(reply(env, "gift"))
    assert state_module._meta_state == before


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
def test_early_terminal_reply_wins_over_late_receipt(env, monkeypatch, kind):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
    else:
        monkeypatch.setattr(concubine, "_send_gift_command", AsyncMock(return_value=True))
    root = ROOT if kind == "gift_bag" else ROOT + 10

    async def sent(_command, **_kwargs):
        receipt(env, kind, root)
        assert await reply(env, kind)
        assert env.identity[FIELD][kind]["status"] == "complete"
        receipt(env, kind, root)
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(send_bag() if kind == "gift_bag" else send_gift(env))
    assert env.identity[FIELD][kind]["status"] == "complete"
    assert (CHAT, root) not in env.identity["pending_tasks"]
    assert env.identity["concubine_phase"] == "idle"


def test_nested_early_bag_result_does_not_overwrite_gift_pending(env):
    async def sent(command, **_kwargs):
        if command == concubine.CMD_STORAGE_BAG:
            receipt(env)
            assert await reply(env)
            receipt(env)
            return SimpleNamespace(id=ROOT, chat_id=CHAT, sent_at=NOW + .2, send_started_at=NOW)
        assert env.identity[FIELD]["gift"]["status"] == "sending"
        return SimpleNamespace(id=ROOT + 10, chat_id=CHAT, sent_at=NOW + 2.2, send_started_at=NOW + 2)

    env.send.side_effect = sent
    assert asyncio.run(send_bag())
    assert env.identity[FIELD]["gift_bag"]["status"] == "complete"
    assert env.identity[FIELD]["gift"]["status"] == "sent"
    assert env.identity["concubine_phase"] == "gift_pending"
    assert env.identity["concubine_gift_msg_id"] == ROOT + 10
    assert env.send.await_count == 2


@pytest.mark.parametrize("mode", ["none", "exception", "cancel"])
def test_uncertain_gift_never_expires_or_retries_on_later_days(env, monkeypatch, mode):
    prepare_gift(env, monkeypatch)
    env.send.return_value = None
    if mode != "none":
        env.send.side_effect = RuntimeError("fixture") if mode == "exception" else asyncio.CancelledError()
        with pytest.raises(RuntimeError if mode == "exception" else asyncio.CancelledError):
            asyncio.run(send_gift(env))
    else:
        assert not asyncio.run(send_gift(env))
    assert env.identity[FIELD]["gift"]["status"] == "unknown"
    for advance in (60, 3600, 86400, 86400 * 7):
        env.clock[0] = NOW + advance
        with state_module.use_identity(ID):
            concubine.restore_concubine_runtime(env.clock[0])
            asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
        assert env.identity[FIELD]["gift"]["status"] == "unknown"
        assert env.identity["concubine_last_gift_day"] == ""
    env.send.assert_awaited_once()


@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_late_gift_receipt_cannot_write_new_owner(env, monkeypatch, change):
    prepare_gift(env, monkeypatch)
    after = {}

    async def sent(_command, **_kwargs):
        if change == "delete":
            state_module.remove_identity(ID)
        elif change == "replace":
            state_module._meta_state["identity_states"][ID] = copy.deepcopy(env.identity)
        else:
            state_module.set_identity_account(ID, ACCOUNT + 1)
        after.update(copy.deepcopy(state_module._meta_state))
        return env.send.return_value

    env.send.side_effect = sent
    assert not asyncio.run(send_gift(env))
    assert state_module._meta_state == after


@pytest.mark.parametrize("change", ["module", "affinity", "schedule", "inventory", "summary"])
def test_gift_queue_does_not_spend_after_plan_changes(env, monkeypatch, change):
    prepare_gift(env, monkeypatch)

    async def sent(_command, **kwargs):
        if change == "module":
            env.identity["concubine_tianji_enabled"] = False
        elif change == "affinity":
            env.identity["concubine_affinity"] = 300
        elif change == "schedule":
            env.identity["next_concubine_time"] = NOW + 40000
        elif change == "summary":
            env.identity.update(deep_retreat_enabled=True, deep_retreat_phase="observing_summary")
        else:
            state_module.get_storage_bag_records()[str(ID)]["items"][STONE] = 0
        assert not kwargs["operation_check"]()
        env.block.update(status="unsent", code="operation_cancelled", at=env.clock[0])
        return None

    env.send.side_effect = sent
    assert not asyncio.run(send_gift(env))
    assert env.identity[FIELD]["gift"]["status"] == "unsent"
    assert env.identity["concubine_last_gift_day"] == ""


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
@pytest.mark.parametrize("change", ["root", "chat", "bot", "time", "message", "command"])
def test_reply_requires_exact_scope_and_server_clock(env, monkeypatch, kind, change):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
        assert asyncio.run(send_gift(env))
    else:
        assert asyncio.run(send_bag())
    root = ROOT if kind == "gift_bag" else ROOT + 10
    args = {"root": root}
    if change == "root":
        args["root"] = root + 1
    elif change == "chat":
        args["current_chat_id"] = CHAT - 1
    elif change == "bot":
        args["reply_context"] = {"sender_id": BOT + 1}
    elif change == "time":
        args["observed_at"] = NOW - 100
    elif change == "message":
        args["current_msg_id"] = root
    else:
        env.identity[FIELD][kind]["command"] = ".unrelated"
    before = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(reply(env, kind, **args))
    assert state_module._meta_state == before


@pytest.mark.parametrize("text", ["working", "@gift_owner \u7684\u50a8\u7269\u888b", BAG.replace("gift_owner", "somebody_else"), BAG.replace("\u7075\u77f3", "unknown")])
def test_incomplete_or_unowned_inventory_never_authorizes_spending(env, text):
    assert asyncio.run(send_bag())
    before = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(reply(env, text=text))
    assert state_module._meta_state == before
    env.send.assert_awaited_once()


@pytest.mark.parametrize("text", ["working", SUCCESS.replace("x60", "x600"), SUCCESS.replace(NAME, "someone"), "\u4eca\u65e5\u8d60\u4e88\u5956\u52b1\u786e\u8ba4\u4e2d"])
def test_unconfirmed_gift_reply_keeps_pending_and_does_not_mark_day(env, monkeypatch, text):
    prepare_gift(env, monkeypatch)
    assert asyncio.run(send_gift(env))
    before = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(reply(env, "gift", text=text))
    assert state_module._meta_state == before


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
@pytest.mark.parametrize("mode", ["false", "exception"])
def test_failed_completion_save_rolls_back_and_withholds_followup(env, monkeypatch, kind, mode):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
        assert asyncio.run(send_gift(env))
    else:
        assert asyncio.run(send_bag())
    receipt(env, kind, ROOT if kind == "gift_bag" else ROOT + 10)
    before = copy.deepcopy(state_module._meta_state)
    env.save.return_value = False
    if mode == "exception":
        env.save.side_effect = RuntimeError("save fixture")
        with pytest.raises(RuntimeError):
            asyncio.run(reply(env, kind))
    else:
        assert not asyncio.run(reply(env, kind))
    assert state_module._meta_state == before
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
def test_exact_operation_cleanup_preserves_replacement_pending(env, monkeypatch, kind):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
        assert asyncio.run(send_gift(env))
    else:
        assert asyncio.run(send_bag())
        monkeypatch.setattr(concubine, "_send_gift_command", AsyncMock(return_value=True))
    root = ROOT if kind == "gift_bag" else ROOT + 10
    receipt(env, kind, root)
    env.identity["pending_tasks"][(CHAT, root)]["op_id"] = "replacement"
    replacement = copy.deepcopy(env.identity["pending_tasks"])
    assert asyncio.run(reply(env, kind))
    assert env.identity["pending_tasks"] == replacement


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
def test_unknown_action_survives_sqlite_reload_and_can_reconcile(env, monkeypatch, kind):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
    env.send.return_value = None
    assert not asyncio.run(send_bag() if kind == "gift_bag" else send_gift(env))
    assert persistence.save_state()
    before = copy.deepcopy(env.identity[FIELD])
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    assert env.identity[FIELD] == before
    if kind == "gift_bag":
        monkeypatch.setattr(concubine, "_send_gift_command", AsyncMock(return_value=True))
    receipt(env, kind, ROOT if kind == "gift_bag" else ROOT + 10)
    assert asyncio.run(reply(env, kind))
    assert persistence.save_state()
    assert persistence.load_state()
    assert state_module.get_identity_state(ID)[FIELD][kind]["status"] == "complete"


@pytest.mark.parametrize("change", ["module", "pause", "disable", "schedule"])
def test_confirmed_gift_accounts_after_disable_without_overwriting_controls(env, monkeypatch, change):
    prepare_gift(env, monkeypatch)
    assert asyncio.run(send_gift(env))
    if change == "module":
        env.identity["concubine_tianji_enabled"] = False
    elif change == "pause":
        state_module.set_global_enabled(False)
    elif change == "disable":
        state_module.set_identity_enabled(ID, False)
    else:
        env.identity["next_concubine_time"] = NOW + 40000
    next_at = env.identity["next_concubine_time"]
    assert asyncio.run(reply(env, "gift"))
    assert env.identity["concubine_affinity"] == 300
    assert env.identity["next_concubine_time"] == next_at
    assert state_module.get_storage_bag_records()[str(ID)]["items"][STONE] == 940


def test_newer_inventory_and_partner_snapshot_are_not_double_adjusted(env, monkeypatch):
    prepare_gift(env, monkeypatch)
    assert asyncio.run(send_gift(env))
    env.identity.update(concubine_affinity=300, concubine_last_snapshot_at=NOW + 10)
    state_module.set_storage_bag_records({str(ID): {"updated_at": NOW + 10, "items": {STONE: 940}}})
    assert asyncio.run(reply(env, "gift"))
    assert env.identity["concubine_affinity"] == 300
    assert state_module.get_storage_bag_records()[str(ID)]["items"][STONE] == 940
    assert env.identity[FIELD]["gift"]["result"]["applied"] is False


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
def test_resetting_scalar_phase_does_not_unlock_an_unknown_action(env, monkeypatch, kind):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
    env.send.return_value = None
    assert not asyncio.run(send_bag() if kind == "gift_bag" else send_gift(env))
    env.identity.update(concubine_phase="idle", concubine_gift_bag_msg_id=0, concubine_gift_msg_id=0, next_concubine_time=0)
    with state_module.use_identity(ID):
        assert concubine.concubine_miniapp_status_block_reason(NOW + 10) == "affinity_action_pending"
        assert not asyncio.run(concubine._send_status_command(NOW + 10))
        assert not asyncio.run(concubine._send_gift_bag_command(NOW + 10))
        assert not asyncio.run(concubine._send_gift_command(NOW + 10, 60))
    env.send.assert_awaited_once()


def test_only_bag_read_can_expire_without_consuming_gift_day(env):
    env.send.return_value = None
    assert not asyncio.run(send_bag())
    env.clock[0] = NOW + concubine.CONCUBINE_PHASE_TIMEOUT_SEC + 1
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(env.clock[0]))
    assert env.identity[FIELD]["gift_bag"]["status"] == "expired"
    assert env.identity["concubine_last_gift_day"] == ""
    assert env.identity["concubine_gift_attempt_day"] == ""
    env.send.assert_awaited_once()


async def routed_reply(env, kind, route):
    root = ROOT if kind == "gift_bag" else ROOT + 10
    record = env.identity[FIELD][kind]
    family = "storage_bag" if kind == "gift_bag" else "concubine_gift"
    event = SimpleNamespace(id=root + 1, chat_id=CHAT, sender_id=BOT, server_event_at=env.clock[0] + 1)
    context = {"send_as_id": ID, "family": family, "root_msg_id": root, "reply_to_msg_id": root,
               "chat_id": CHAT, "reply_to_command": record["command"]}
    text = BAG if kind == "gift_bag" else SUCCESS
    if route == "native":
        parent = SimpleNamespace(id=root, chat_id=CHAT, raw_text=record["command"])
        return await app._handle_routed_reply_event(event, text, env.clock[0] + 2, parent, context)
    return await passive_inbox.handle_passive_module_card(
        text, now=env.clock[0] + 2, reply_context=context, event=event, event_type="message")


@pytest.mark.parametrize("route", ["native", "passive"])
@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
def test_real_reply_paths_share_owned_completion(env, monkeypatch, route, kind):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
        assert asyncio.run(send_gift(env))
    else:
        assert asyncio.run(send_bag())
        monkeypatch.setattr(concubine, "_send_gift_command", AsyncMock(return_value=True))
    receipt(env, kind, ROOT if kind == "gift_bag" else ROOT + 10)
    assert asyncio.run(routed_reply(env, kind, route))
    assert env.identity[FIELD][kind]["status"] == "complete"
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(routed_reply(env, kind, route))
    assert env.identity == before


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
@pytest.mark.parametrize("field,value", [
    ("send_as_id", ID + 1), ("account_id", ACCOUNT + 1), ("chat_id", CHAT - 1),
    ("root_msg_id", ROOT + 20), ("reply_to_msg_id", ROOT + 20),
    ("reply_to_command_edited", True), ("reply_to_command", ".unrelated"),
])
def test_explicit_reply_context_cannot_contradict_owned_action(env, monkeypatch, kind, field, value):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
        assert asyncio.run(send_gift(env))
    else:
        assert asyncio.run(send_bag())
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind, reply_context={"sender_id": BOT, field: value}))
    assert env.identity == before


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
def test_passive_completion_can_retry_a_failed_local_save(env, monkeypatch, kind):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
        assert asyncio.run(send_gift(env))
    else:
        assert asyncio.run(send_bag())
        monkeypatch.setattr(concubine, "_send_gift_command", AsyncMock(return_value=True))
    env.save.return_value = False
    assert not asyncio.run(routed_reply(env, kind, "passive"))
    assert env.identity[FIELD][kind]["status"] == "sent"
    env.save.return_value = True
    assert asyncio.run(routed_reply(env, kind, "passive"))
    assert env.identity[FIELD][kind]["status"] == "complete"


def logged_reply(kind="gift_bag", **changes):
    root = ROOT if kind == "gift_bag" else ROOT + 10
    entry = dict(event_type="message", text=BAG if kind == "gift_bag" else SUCCESS,
                 sender_is_bot=True, sender_id=BOT, chat_id=CHAT, reply_to_msg_id=root,
                 message_id=root + 1, server_event_at=NOW + 3, ts_epoch=NOW + 3)
    entry.update(changes)
    return entry


def tick(env, now):
    env.clock[0] = now
    with state_module.use_identity(ID):
        asyncio.run(concubine.run_concubine_scheduler(now))


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
def test_log_recovery_replays_confirmed_result_not_just_receipt(env, monkeypatch, kind):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
        assert asyncio.run(send_gift(env))
    else:
        assert asyncio.run(send_bag())
        monkeypatch.setattr(concubine, "_send_gift_command", AsyncMock(return_value=True))
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[logged_reply(kind)]))
    tick(env, NOW + 61)
    assert env.identity[FIELD][kind]["status"] == "complete"
    assert env.identity["concubine_affinity"] == (300 if kind == "gift" else 240)
    env.send.assert_awaited_once()


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
@pytest.mark.parametrize("change", ["player", "chat", "root", "clock", "newer_incomplete", "same_clock_conflict"])
def test_recovery_rejects_untrusted_or_obsolete_log_text(env, monkeypatch, kind, change):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
        assert asyncio.run(send_gift(env))
    else:
        assert asyncio.run(send_bag())
    entries = [logged_reply(kind)]
    if change == "player":
        entries[0]["sender_is_bot"] = False
    elif change == "chat":
        entries[0]["chat_id"] = CHAT - 1
    elif change == "root":
        entries[0]["reply_to_msg_id"] = ROOT + 100
    elif change == "clock":
        entries[0]["server_event_at"] = NOW - 2
    elif change == "newer_incomplete":
        entries.append(logged_reply(kind, text="working", event_type="edit", server_event_at=NOW + 4))
    else:
        entries.append(logged_reply(kind, text="conflict", event_type="edit"))
    lookup = Mock(return_value=entries)
    monkeypatch.setattr(concubine, "find_message_log_replies", lookup)
    tick(env, NOW + 61)
    calls = lookup.call_count
    tick(env, NOW + 62)
    assert lookup.call_count == calls
    assert env.identity[FIELD][kind]["status"] == "sent"
    assert env.identity["concubine_affinity"] == 240
    env.send.assert_awaited_once()


@pytest.mark.parametrize("field,value", [
    ("status", []), ("amount", True), ("amount", 60.5), ("started_at", float("nan")),
    ("msg_id", float(ROOT)), ("account_id", "8301"), ("kind", []), ("plan_key", "broken"),
])
def test_malformed_action_is_held_without_side_effects(env, field, value):
    assert asyncio.run(send_bag())
    env.identity[FIELD]["gift_bag"][field] = value
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert concubine_affinity_actions.block_reason() == "invalid"
        concubine.restore_concubine_runtime(NOW + 1000)
        asyncio.run(concubine.run_concubine_scheduler(NOW + 1000))
    assert env.identity == before
    env.send.assert_awaited_once()


def test_queue_operation_check_uses_captured_identity_context(env):
    state_module.set_identity_account(ID + 1, ACCOUNT)
    state_module.get_identity_state(ID + 1).update(deep_retreat_enabled=True, deep_retreat_phase="observing_summary")

    async def sent(_command, **kwargs):
        with state_module.use_identity(ID + 1):
            assert kwargs["operation_check"]()
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(send_bag())


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
def test_real_runtime_receipt_without_account_metadata_is_adopted(env, monkeypatch, kind):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
    env.send.return_value = None
    assert not asyncio.run(send_bag() if kind == "gift_bag" else send_gift(env))
    record = env.identity[FIELD][kind]
    root = ROOT if kind == "gift_bag" else ROOT + 10
    with state_module.use_identity(ID):
        runtime._finalize_game_command_sent(
            record["command"], msg_id=root, sent_at=env.clock[0] + .2, send_started_at=env.clock[0],
            send_as_id=ID, game_group_id=CHAT, topic_id=0, track=True, max_retry=0, append_sent_log=False,
            reply_timeout=concubine.CONCUBINE_PHASE_TIMEOUT_SEC,
            send_intent={"op_id": record["op_id"], "source_module": "concubine_gift"},
        )
    pending = env.identity["pending_tasks"][(CHAT, root)]
    assert "account_id" not in pending
    if kind == "gift_bag":
        monkeypatch.setattr(concubine, "_send_gift_command", AsyncMock(return_value=True))
    assert asyncio.run(reply(env, kind))
    assert env.identity[FIELD][kind]["status"] == "complete"
    assert (CHAT, root) not in env.identity["pending_tasks"]


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
@pytest.mark.parametrize("bad", ["operation", "account", "chat", "fractional", "multiple"])
def test_receipt_adoption_rejects_contradictory_or_ambiguous_pending(env, monkeypatch, kind, bad):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
    env.send.return_value = None
    assert not asyncio.run(send_bag() if kind == "gift_bag" else send_gift(env))
    root = ROOT if kind == "gift_bag" else ROOT + 10
    receipt(env, kind, root)
    pending = env.identity["pending_tasks"]
    if bad == "operation":
        pending[(CHAT, root)]["op_id"] = "another"
    elif bad == "account":
        pending[(CHAT, root)]["account_id"] = ACCOUNT + 1
    elif bad == "chat":
        item = pending.pop((CHAT, root))
        item["chat_id"] = CHAT - 1
        pending[CHAT - 1, root] = item
    elif bad == "fractional":
        pending[CHAT, root + .5] = pending.pop((CHAT, root))
    else:
        pending[CHAT, root + 20] = dict(pending[CHAT, root], message_id=root + 20)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind))
    assert env.identity == before


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
def test_old_receipt_and_terminal_do_not_reopen_after_reload(env, monkeypatch, kind):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
        assert asyncio.run(send_gift(env))
    else:
        assert asyncio.run(send_bag())
        monkeypatch.setattr(concubine, "_send_gift_command", AsyncMock(return_value=True))
    assert asyncio.run(reply(env, kind))
    assert persistence.save_state()
    assert persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, kind))
    assert env.identity == before


def test_passive_old_bag_reply_does_not_overwrite_inventory(env, monkeypatch):
    assert asyncio.run(send_bag())
    monkeypatch.setattr(concubine, "_send_gift_command", AsyncMock(return_value=True))
    assert asyncio.run(reply(env))
    before = copy.deepcopy(state_module.get_storage_bag_records())
    context = {"send_as_id": ID, "family": "storage_bag", "root_msg_id": ROOT - 10, "reply_to_msg_id": ROOT - 10}
    event = SimpleNamespace(id=ROOT - 9, chat_id=CHAT, sender_id=BOT, server_event_at=NOW - 10)
    assert not asyncio.run(passive_inbox.handle_passive_module_card(
        BAG.replace("1,000", "9,999"), now=NOW + 10, reply_context=context, event=event, event_type="message"))
    assert state_module.get_storage_bag_records() == before


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
def test_failed_intent_save_exception_is_definitely_unsent(env, monkeypatch, kind):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
    env.save.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        asyncio.run(send_bag() if kind == "gift_bag" else send_gift(env))
    env.send.assert_not_awaited()
    assert env.identity[FIELD][kind]["status"] == "unsent"


@pytest.mark.parametrize("value", [None, [], {"extra": {}}, {"gift": {}}])
def test_malformed_container_blocks_sends_and_survives_restore(env, value):
    env.identity[FIELD] = value
    with state_module.use_identity(ID):
        assert concubine_affinity_actions.block_reason() == "invalid"
        concubine.restore_concubine_runtime(NOW)
        asyncio.run(concubine.run_concubine_scheduler(NOW))
    assert env.identity[FIELD] == value
    env.send.assert_not_awaited()


def test_shortage_is_terminal_but_not_a_successful_gift(env, monkeypatch):
    prepare_gift(env, monkeypatch)
    assert asyncio.run(send_gift(env))
    assert asyncio.run(reply(env, "gift", text="\u7075\u77f3\u4e0d\u8db3"))
    assert env.identity[FIELD]["gift"]["result"]["outcome"] == "shortage"
    assert env.identity["concubine_last_gift_day"] == ""
    assert env.identity["concubine_gift_attempt_day"] == concubine._local_day_key(NOW)
    assert state_module.get_storage_bag_records()[str(ID)]["items"][STONE] == 1000


def test_delayed_previous_day_gift_does_not_mark_the_delivery_day(env, monkeypatch):
    prepare_gift(env, monkeypatch)
    assert asyncio.run(send_gift(env))
    env.clock[0] = NOW + 86400
    assert asyncio.run(reply(env, "gift", observed_at=NOW + 3))
    assert env.identity["concubine_last_gift_day"] == concubine._local_day_key(NOW)


@pytest.mark.parametrize("mode", ["false", "exception"])
@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
def test_affinity_recovery_checkpoint_failure_restores_original_record(env, monkeypatch, mode, kind):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
        assert asyncio.run(send_gift(env))
    else:
        assert asyncio.run(send_bag())
    before = copy.deepcopy(env.identity)
    scan = Mock(return_value=[logged_reply(kind)])
    monkeypatch.setattr(concubine, "find_message_log_replies", scan)
    env.save.reset_mock()
    if mode == "exception":
        env.save.side_effect = OSError("checkpoint")
        with pytest.raises(OSError):
            tick(env, NOW + 1001)
    else:
        env.save.return_value = False
        tick(env, NOW + 1001)
    assert env.identity == before
    scan.assert_not_called()
    env.save.assert_called_once()


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_gift_bag_valid_result_is_not_expired_after_failed_completion_save(env, monkeypatch, mode):
    assert asyncio.run(send_bag())
    receipt(env)
    monkeypatch.setattr(concubine, "find_message_log_replies", Mock(return_value=[logged_reply()]))
    env.save.reset_mock()
    env.save.side_effect = [True, False if mode == "false" else OSError("completion"), True]
    if mode == "exception":
        with pytest.raises(OSError):
            tick(env, NOW + 1001)
    else:
        tick(env, NOW + 1001)
    assert env.save.call_count == 2
    assert env.identity[FIELD]["gift_bag"]["status"] == "sent"
    assert (CHAT, ROOT) in env.identity["pending_tasks"]
    env.save.side_effect = None
    tick(env, NOW + 1062)
    assert env.identity[FIELD]["gift_bag"]["status"] == "complete"
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    env.send.assert_awaited_once()


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_gift_bag_expiry_save_failure_restores_pending_read(env, mode):
    assert asyncio.run(send_bag())
    receipt(env)
    before = copy.deepcopy(env.identity)
    env.save.side_effect = [True, False if mode == "false" else OSError("expiry")]
    if mode == "exception":
        with pytest.raises(OSError):
            tick(env, NOW + 1001)
    else:
        tick(env, NOW + 1001)
    before[FIELD]["gift_bag"]["replay_after"] = NOW + 1001 + concubine.CONCUBINE_QUERY_REPLAY_SEC
    assert env.identity == before


@pytest.mark.parametrize("kind", ["gift_bag", "gift"])
@pytest.mark.parametrize("mode", ["success", "false", "exception"])
def test_late_affinity_pending_cleanup_is_owned_and_transactional(env, monkeypatch, kind, mode):
    if kind == "gift":
        prepare_gift(env, monkeypatch)
        assert asyncio.run(send_gift(env))
    else:
        assert asyncio.run(send_bag())
        monkeypatch.setattr(concubine, "_send_gift_command", AsyncMock(return_value=True))
    assert asyncio.run(reply(env, kind))
    root = ROOT if kind == "gift_bag" else ROOT + 10
    receipt(env, kind, root)
    env.identity["pending_tasks"][(CHAT - 1, root)] = dict(
        env.identity["pending_tasks"][(CHAT, root)], chat_id=CHAT - 1)
    before = copy.deepcopy(env.identity)
    if mode == "false":
        env.save.return_value = False
    elif mode == "exception":
        env.save.side_effect = OSError("cleanup")
    with state_module.use_identity(ID):
        if mode == "exception":
            with pytest.raises(OSError):
                asyncio.run(concubine_affinity_actions.recover(NOW + 30))
        else:
            assert asyncio.run(concubine_affinity_actions.recover(NOW + 30))
    if mode == "success":
        assert (CHAT, root) not in env.identity["pending_tasks"]
        assert (CHAT - 1, root) in env.identity["pending_tasks"]
    else:
        assert env.identity == before


def test_rebound_account_expires_read_without_changing_new_schedule(env):
    assert asyncio.run(send_bag())
    state_module.set_identity_account(ID, ACCOUNT + 1)
    env.identity["next_concubine_time"] = NOW + 40000
    tick(env, NOW + 1000)
    assert env.identity[FIELD]["gift_bag"]["status"] == "expired"
    assert env.identity["next_concubine_time"] == NOW + 40000
    env.send.assert_awaited_once()


@pytest.mark.parametrize("outcome", ["success", "shortage", "daily_limit"])
def test_completed_gift_facts_survive_reset_of_daily_scalar_markers(env, monkeypatch, outcome):
    prepare_gift(env, monkeypatch)
    assert asyncio.run(send_gift(env))
    text = {"success": SUCCESS, "shortage": "\u7075\u77f3\u4e0d\u8db3", "daily_limit": "\u4eca\u65e5\u5df2\u8d60\u4e88"}[outcome]
    assert asyncio.run(reply(env, "gift", text=text))
    env.identity.update(concubine_phase="idle", concubine_affinity=240, concubine_last_gift_day="", concubine_gift_attempt_day="")
    env.clock[0] = NOW + 10
    with state_module.use_identity(ID):
        assert not concubine._is_gift_recovery_due(env.clock[0])
        assert not asyncio.run(concubine._send_gift_bag_command(env.clock[0]))
    env.send.assert_awaited_once()
