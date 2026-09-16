import asyncio
import copy
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import wanxin
from yinluo_native_support import BOT, CHAT, native_logs, native_reply


OWNER = 301299112
HELPER = 8613500668
NOW = 1_800_000_000.0


@pytest.fixture
def env(monkeypatch):
    before = copy.deepcopy(state_module._meta_state)
    state_module._meta_state["identity_ids"] = []
    state_module._meta_state["identity_states"] = {}
    state_module._meta_state["identity_account_map"] = {}
    state_module._meta_state["send_as_profiles"] = {}
    state_module._meta_state["global_enabled"] = True
    state_module.set_game_group_id(CHAT)
    state_module.set_game_bot_ids([BOT])
    for identity, username, sect in ((OWNER, "owner", ""), (HELPER, "helper", "阴罗宗")):
        state_module.ensure_identity_registered(identity)
        state_module.set_identity_account(identity, identity)
        state_module.update_send_as_profile(identity, username=username, sect_name=sect, enabled=True)
        state_module.get_identity_state(identity)["wanxin_enabled"] = True
    owner = state_module.get_identity_state(OWNER)
    owner["wanxin_observation"] = wanxin.normalize_wanxin_observation({
        "auto_next_time": NOW - 1,
        "next_visit_time": NOW - 1,
        "next_protect_time": NOW + 3600,
        "next_deduce_time": NOW + 3600,
        "auto_config": {"moon_greet_enabled": False},
        "assist": {"send_as_id": HELPER},
    })
    monkeypatch.setattr(wanxin, "save_state", Mock(return_value=True))
    monkeypatch.setattr(wanxin, "send_audit_log", AsyncMock())
    monkeypatch.setattr(wanxin, "get_phaseful_summary_risk_reason", lambda _now: "")
    monkeypatch.setattr(wanxin, "_iter_message_log_entries_between", lambda *_args: iter(()))
    monkeypatch.setattr(wanxin, "classify_game_send_block", lambda *_args: {"status": "unsent", "code": "operation_invalid"})
    monkeypatch.setattr(wanxin.time, "time", lambda: NOW + 5)
    yield SimpleNamespace(owner=owner, helper=state_module.get_identity_state(HELPER), monkeypatch=monkeypatch)
    state_module._meta_state.clear()
    state_module._meta_state.update(before)


def receipt(msg_id=100, now=NOW):
    return SimpleNamespace(id=msg_id, chat_id=CHAT, sent_at=now, send_started_at=now)


async def schedule(now=NOW):
    with state_module.use_identity(OWNER):
        await wanxin.run_wanxin_scheduler(now)


def prepare_accept(env):
    observed = env.owner["wanxin_observation"]
    observed["next_visit_time"] = NOW + 3600
    observed["commission"].update(id=5, published_at=NOW - 100, publish_msg_id=50, owner_username="owner")


def test_sender_sees_persisted_intent_before_await(env):
    async def send(command, **kwargs):
        pending = env.owner["wanxin_observation"]["pending"]
        assert pending["action"] == "visit"
        assert pending["msg_id"] == 0
        assert pending["status"] == "sending"
        assert pending["account_id"] == OWNER
        assert pending["op_id"] == kwargs["op_id"]
        assert kwargs["target_chat_id"] == CHAT
        assert kwargs["operation_check"]()
        return receipt()

    env.monkeypatch.setattr(wanxin, "send_game_command", send)
    asyncio.run(schedule())
    pending = env.owner["wanxin_observation"]["pending"]
    assert pending["msg_id"] == 100
    assert pending["chat_id"] == CHAT
    assert pending["status"] == "sent"


@pytest.mark.parametrize("action", ["visit", "accept", "identify"])
@pytest.mark.parametrize("change", ["disable", "global_pause", "owner_rebind", "owner_replace", "config_change"])
def test_queued_operation_rechecks_business_and_owner(env, action, change):
    if action != "visit":
        prepare_accept(env)
    if action == "identify":
        env.owner["wanxin_observation"]["commission"].update(accepted=True, accepted_at=NOW - 80, helper_username="helper")

    async def send(command, **kwargs):
        assert callable(kwargs.get("operation_check"))
        assert kwargs["operation_check"]()
        if change == "disable":
            env.owner["wanxin_enabled"] = False
        elif change == "global_pause":
            state_module._meta_state["global_enabled"] = False
        elif change == "owner_rebind":
            state_module.set_identity_account(OWNER, HELPER)
        elif change == "owner_replace":
            state_module._meta_state["identity_states"][OWNER] = copy.deepcopy(env.owner)
        else:
            env.owner["wanxin_observation"]["auto_config"]["visit_enabled" if action == "visit" else "assist_enabled"] = False
        expected = copy.deepcopy(state_module.get_identity_state(OWNER))
        assert not kwargs["operation_check"]()
        return None, expected

    expected = []

    async def transport(command, **kwargs):
        msg, changed = await send(command, **kwargs)
        expected.append(changed)
        return msg

    env.monkeypatch.setattr(wanxin, "send_game_command", transport)
    asyncio.run(schedule())
    assert expected
    current = state_module.get_identity_state(OWNER)
    if change in {"owner_rebind", "owner_replace"}:
        assert current == expected[0]
    elif change == "config_change":
        assert not current["wanxin_observation"]["auto_config"]["visit_enabled" if action == "visit" else "assist_enabled"]
    elif change == "disable":
        assert not current["wanxin_enabled"]


def test_reentry_cannot_send_same_action_while_receipt_is_pending(env):
    calls = []

    async def send(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            await schedule(NOW + 1)
        return receipt(100 + len(calls))

    env.monkeypatch.setattr(wanxin, "send_game_command", send)
    asyncio.run(schedule())
    assert calls == [".探望南宫婉"]


def test_receipt_does_not_overwrite_an_early_result(env):
    async def send(command, **kwargs):
        current = copy.deepcopy(env.owner["wanxin_observation"])
        current["pending"] = {}
        current["next_visit_time"] = NOW + 24 * 3600
        current["auto_last_result"] = "early native completion"
        current["auto_next_time"] = NOW + 20
        env.owner["wanxin_observation"] = current
        return receipt()

    env.monkeypatch.setattr(wanxin, "send_game_command", send)
    asyncio.run(schedule())
    observed = env.owner["wanxin_observation"]
    assert observed["pending"] == {}
    assert observed["next_visit_time"] == NOW + 24 * 3600
    assert observed["auto_last_result"] == "early native completion"


def test_accept_receipt_cannot_attach_to_replacement_commission(env):
    prepare_accept(env)

    async def send(command, **kwargs):
        current = copy.deepcopy(env.owner["wanxin_observation"])
        current["commission"].update(id=6, published_at=NOW + 1, publish_msg_id=102)
        current["pending"] = {}
        env.owner["wanxin_observation"] = current
        return receipt()

    env.monkeypatch.setattr(wanxin, "send_game_command", send)
    asyncio.run(schedule())
    assert env.owner["wanxin_observation"]["commission"]["id"] == 6
    assert env.owner["wanxin_observation"]["commission"]["accept_msg_id"] == 0
    assert not env.owner["wanxin_observation"]["pending"]


@pytest.mark.parametrize("raise_cancel", [False, True])
def test_no_receipt_retains_unknown_instead_of_inventing_cooldown(env, raise_cancel):
    async def send(command, **kwargs):
        if raise_cancel:
            raise asyncio.CancelledError
        return None

    env.monkeypatch.setattr(wanxin, "send_game_command", send)
    env.monkeypatch.setattr(wanxin, "classify_game_send_block", lambda *_args: {"status": "unknown", "code": "send_timeout"})
    if raise_cancel:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(schedule())
    else:
        asyncio.run(schedule())
    observed = env.owner["wanxin_observation"]
    assert observed["pending"]["status"] == "unknown"
    assert observed["pending"]["msg_id"] == 0
    assert observed["next_visit_time"] == NOW - 1
    assert wanxin.normalize_wanxin_observation(observed)["pending"] == observed["pending"]


@pytest.mark.parametrize("replacement", ["pending", "owner", "account"])
def test_cleanup_does_not_write_through_state_changed_during_recovery(env, replacement):
    env.owner["wanxin_observation"]["pending"] = {
        "action": "visit", "family": "wanxin_visit", "msg_id": 100, "chat_id": CHAT,
        "send_as_id": OWNER, "sent_at": NOW - 200, "reply_due_at": NOW - 100,
    }
    expected = []

    async def recover(observed, now, *, pending=None, entries=None):
        if replacement == "owner":
            state_module._meta_state["identity_states"][OWNER] = state_module.new_identity_state()
        elif replacement == "account":
            state_module.set_identity_account(OWNER, HELPER)
        else:
            env.owner["wanxin_observation"]["pending"].update(action="deduce", family="wanxin_deduce", msg_id=200)
        expected.append(copy.deepcopy(state_module.get_identity_state(OWNER)))
        observed["pending"] = {}
        return True

    env.monkeypatch.setattr(wanxin, "_recover_wanxin_pending_from_message_log", recover)
    with state_module.use_identity(OWNER):
        asyncio.run(wanxin._cleanup_wanxin_pending_only(NOW))
    assert state_module.get_identity_state(OWNER) == expected[0]


def test_unproven_cancel_does_not_cancel_or_starve_independent_owner_action(env):
    env.owner["wanxin_observation"]["commission"].update(
        id=5, published_at=NOW - 90000, claimed_elsewhere=True, cancel_due_at=NOW - 1,
    )
    sender = AsyncMock(return_value=receipt())
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule())
    assert sender.await_args.args[0] == ".探望南宫婉"
    assert env.owner["wanxin_observation"]["commission"]["id"] == 5


def test_wrong_chat_reply_cannot_clear_pending(env):
    env.owner["wanxin_observation"]["pending"] = {
        "action": "visit", "family": "wanxin_visit", "msg_id": 100, "chat_id": CHAT,
        "send_as_id": OWNER, "sent_at": NOW - 2, "reply_due_at": NOW + 90,
    }
    before = copy.deepcopy(env.owner)
    text = "【探望南宫婉】\n你以月殿旧令稳住她的神魂，南宫婉短暂醒转片刻。"
    event = native_reply(OWNER, ".探望南宫婉", text, NOW, root=100, chat=CHAT - 1)
    with state_module.use_identity(OWNER):
        handled = asyncio.run(wanxin.handle_wanxin_reply(
            text, NOW, SimpleNamespace(id=100, raw_text=".探望南宫婉"),
            matched_family="wanxin_visit", result_msg_id=101, event=event,
        ))
    assert not handled
    assert env.owner == before


def visit_event(*, at=NOW + 1, root=100):
    return native_reply(
        OWNER, ".探望南宫婉", "【探望南宫婉】\n你以月殿旧令稳住她的神魂，南宫婉短暂醒转片刻。",
        at, root=root, command_at=NOW,
    )


async def deliver(event, now=NOW + 5):
    with state_module.use_identity(OWNER):
        return await wanxin.handle_wanxin_reply(event.text, now, event=event, result_msg_id=event.msg_id)


def test_exact_early_native_reply_is_not_overwritten_by_send_receipt(env):
    async def send(command, **kwargs):
        env.owner["pending_tasks"][(CHAT, 100)] = {
            "cmd": command, "family": "wanxin_visit", "op_id": kwargs["op_id"],
            "account_id": OWNER, "chat_id": CHAT, "sent_at": NOW,
        }
        assert await deliver(visit_event())
        assert not env.owner["wanxin_observation"]["pending"]
        return receipt()

    env.monkeypatch.setattr(wanxin, "send_game_command", send)
    asyncio.run(schedule())
    observed = env.owner["wanxin_observation"]
    assert not observed["pending"]
    assert observed["next_visit_time"] > NOW
    assert observed["auto_last_result"] == "探望成功"


def test_native_completion_after_disable_keeps_switch_off(env):
    env.monkeypatch.setattr(wanxin, "send_game_command", AsyncMock(return_value=receipt()))
    asyncio.run(schedule())
    env.owner["wanxin_enabled"] = False
    assert asyncio.run(deliver(visit_event()))
    assert not env.owner["wanxin_enabled"]
    assert not env.owner["wanxin_observation"]["pending"]


@pytest.mark.parametrize("corruption", ["bot", "command_actor", "account", "command_time", "edited_command", "future", "family"])
def test_native_reply_requires_original_command_and_official_provenance(env, corruption):
    env.monkeypatch.setattr(wanxin, "send_game_command", AsyncMock(return_value=receipt()))
    asyncio.run(schedule())
    event = visit_event()
    context = dict(event.reply_context)
    family = None
    if corruption == "bot":
        event = replace(event, sender_id=BOT + 1)
    elif corruption == "command_actor":
        context["reply_to_sender_id"] = HELPER
    elif corruption == "account":
        context["account_id"] = HELPER
    elif corruption == "command_time":
        context["reply_to_server_at"] = 0
    elif corruption == "edited_command":
        context["reply_to_command_edited"] = True
    elif corruption == "future":
        event = replace(event, server_event_at=NOW + 1000)
    else:
        family = "wanxin_deduce"
    event = replace(event, reply_context=context)
    before = copy.deepcopy(env.owner)
    with state_module.use_identity(OWNER):
        assert not asyncio.run(wanxin.handle_wanxin_reply(event.text, NOW + 5, event=event, matched_family=family))
    assert env.owner == before


def test_manual_reply_preserves_unrelated_pending_and_guards(env):
    env.owner["wanxin_observation"]["pending"] = {
        "action": "deduce", "family": "wanxin_deduce", "msg_id": 200, "chat_id": CHAT,
        "send_as_id": OWNER, "sent_at": NOW - 5, "reply_due_at": NOW + 85,
    }
    env.owner["pending_tasks"][(CHAT, 200)] = {"cmd": ".推演封魂咒", "chat_id": CHAT, "sent_at": NOW - 5}
    sibling = copy.deepcopy(env.owner["pending_tasks"])
    assert asyncio.run(deliver(visit_event()))
    assert env.owner["wanxin_observation"]["pending"]["msg_id"] == 200
    assert env.owner["pending_tasks"] == sibling
    assert env.owner["wanxin_observation"]["next_visit_time"] > NOW


def test_duplicate_and_cosmetic_edit_do_not_repeat_affinity_or_cooldown(env):
    env.owner["concubine_affinity"] = 120
    text = "【婉影问安】\n情缘 +9。\n婉心 115 | 魂封 0 | 月魄 38 | 咒源 120"
    event = native_reply(OWNER, ".婉影问安", text, NOW + 1, root=100, command_at=NOW)
    assert asyncio.run(deliver(event))
    due = env.owner["wanxin_observation"]["next_moon_greet_time"]
    for revision in (event, replace(event, event_type="edit", server_event_at=NOW + 3, text=text + "\n")):
        assert asyncio.run(deliver(revision))
    assert env.owner["concubine_affinity"] == 129
    assert env.owner["wanxin_observation"]["next_moon_greet_time"] == due


@pytest.mark.parametrize("cosmetic_edit", [False, True])
@pytest.mark.parametrize("restore_business_pending", [False, True])
def test_duplicate_completion_closes_only_its_exact_pending_root(env, cosmetic_edit, restore_business_pending):
    env.monkeypatch.setattr(wanxin, "send_game_command", AsyncMock(return_value=receipt()))
    asyncio.run(schedule())
    pending = copy.deepcopy(env.owner["wanxin_observation"]["pending"])
    event = visit_event()
    assert asyncio.run(deliver(event))
    due = env.owner["wanxin_observation"]["next_visit_time"]
    if restore_business_pending:
        env.owner["wanxin_observation"]["pending"] = pending
    env.owner["pending_tasks"][(CHAT, 100)] = {
        "cmd": ".探望南宫婉", "family": "wanxin_visit", "chat_id": CHAT,
        "account_id": OWNER, "sent_at": NOW, "op_id": pending["op_id"],
    }
    sibling = {"cmd": ".探望南宫婉", "family": "wanxin_visit", "chat_id": CHAT, "sent_at": NOW + 10}
    env.owner["pending_tasks"][(CHAT, 200)] = sibling
    if cosmetic_edit:
        event = replace(event, event_type="edit", server_event_at=NOW + 3, text=event.text + "\n")
    assert asyncio.run(deliver(event))
    assert not env.owner["wanxin_observation"]["pending"]
    assert env.owner["wanxin_observation"]["next_visit_time"] == due
    assert env.owner["pending_tasks"] == {(CHAT, 200): sibling}


def test_old_command_edit_cannot_replace_a_newer_completion(env):
    env.owner["concubine_affinity"] = 120
    text = "【婉影问安】\n情缘 +9。"
    first = native_reply(OWNER, ".婉影问安", text, NOW + 1, root=100, command_at=NOW)
    newer = native_reply(OWNER, ".婉影问安", text, NOW + 4, root=200, command_at=NOW + 3)
    assert asyncio.run(deliver(first))
    assert asyncio.run(deliver(newer))
    before = copy.deepcopy(env.owner)
    old_edit = replace(first, event_type="edit", server_event_at=NOW + 7, text=text.replace("+9", "+50"))
    assert not asyncio.run(deliver(old_edit, NOW + 8))
    assert env.owner == before


def test_new_command_reply_is_not_hidden_by_later_edit_of_previous_command(env):
    env.owner["concubine_affinity"] = 120
    text = "【婉影问安】\n情缘 +9。"
    first = native_reply(OWNER, ".婉影问安", text, NOW + 1, root=100, command_at=NOW)
    assert asyncio.run(deliver(first))
    assert asyncio.run(deliver(replace(first, event_type="edit", server_event_at=NOW + 4, text=text + "\n")))
    newer = native_reply(OWNER, ".婉影问安", text, NOW + 3, root=200, command_at=NOW + 2)
    assert asyncio.run(deliver(newer))
    assert env.owner["concubine_affinity"] == 138
    assert env.owner["wanxin_observation"]["reply_points"]["moon_greet"]["root"] == 200
    assert env.owner["wanxin_observation"]["next_moon_greet_time"] == NOW + 3 + wanxin.WANXIN_MOON_GREET_CD_SEC + wanxin.CD_BUFFER_SEC


def test_known_unsent_without_reason_does_not_fabricate_business_cooldown(env):
    env.monkeypatch.setattr(wanxin, "send_game_command", AsyncMock(return_value=None))
    classify = Mock(return_value={"status": "unsent", "code": ""})
    env.monkeypatch.setattr(wanxin, "classify_game_send_block", classify)
    asyncio.run(schedule())
    classify.assert_called_once()
    observed = env.owner["wanxin_observation"]
    assert not observed["pending"]
    assert observed["next_visit_time"] == NOW - 1
    assert observed["auto_next_time"] == NOW + wanxin.RETRY_MAX_SEC


@pytest.mark.parametrize("corruption", [
    "root", "point", "command_chat", "command_edit", "handled", "fingerprint",
    "completion", "affinity_delta", "commission_id", "extra_data",
])
def test_malformed_reply_checkpoint_holds_dispatch_and_does_not_mutate_input(env, corruption):
    assert asyncio.run(deliver(visit_event()))
    observed = copy.deepcopy(env.owner["wanxin_observation"])
    point = observed["reply_points"]["visit"]
    if corruption == "root":
        point["root"] = True
    elif corruption == "point":
        point["point"] = []
    elif corruption == "command_chat":
        point["command_point"]["evidence"]["chat_id"] = CHAT - 1
    elif corruption == "command_edit":
        point["command_point"]["evidence"]["edited"] = True
    elif corruption == "handled":
        point["handled"] = "yes"
    elif corruption == "fingerprint":
        point["fingerprint"] = "not_a_digest"
    elif corruption == "completion":
        point["completed_point"] = {**point["completed_point"], "at": NOW + 1000}
    elif corruption == "affinity_delta":
        point["affinity_delta"] = True
    elif corruption == "commission_id":
        point["commission_id"] = True
    else:
        point["extra_data"] = {"unbounded": "unsupported"}
    original = copy.deepcopy(observed)
    normalized = wanxin.normalize_wanxin_observation(observed)
    assert normalized.get("reply_points_invalid")
    assert observed == original
    env.owner["wanxin_observation"] = normalized
    normalized["next_visit_time"] = NOW - 1
    normalized["auto_next_time"] = NOW - 1
    sender = AsyncMock()
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule())
    sender.assert_not_awaited()
    assert env.owner["wanxin_observation"]["auto_next_time"] > NOW
    assert env.owner["wanxin_observation"]["auto_last_error"]


def test_reply_checkpoint_roundtrip_keeps_dedupe_without_aliasing(env):
    env.owner["concubine_affinity"] = 120
    event = native_reply(OWNER, ".婉影问安", "【婉影问安】\n情缘 +9。", NOW + 1, root=100, command_at=NOW)
    assert asyncio.run(deliver(event))
    observed = json.loads(json.dumps(env.owner["wanxin_observation"]))
    env.owner["wanxin_observation"] = wanxin.normalize_wanxin_observation(observed)
    original = copy.deepcopy(observed)
    assert asyncio.run(deliver(replace(event, event_type="edit", server_event_at=NOW + 3, text=event.text + "\n")))
    assert observed == original
    assert env.owner["concubine_affinity"] == 129
    assert env.owner["wanxin_observation"]["reply_points"]["moon_greet"]["point"]["at"] == NOW + 3


@pytest.mark.parametrize("corruption", ["account", "command"])
def test_completion_does_not_clear_a_conflicting_shared_pending_record(env, corruption):
    shared = {
        "cmd": ".探望南宫婉", "chat_id": CHAT, "account_id": OWNER,
        "family": "wanxin_visit", "sent_at": NOW,
    }
    if corruption == "account":
        shared["account_id"] = HELPER
    else:
        shared["cmd"] += " invalid"
    env.owner["pending_tasks"][(CHAT, 100)] = shared
    assert asyncio.run(deliver(visit_event()))
    assert env.owner["pending_tasks"] == {(CHAT, 100): shared}


def test_cooldown_semantic_edit_uses_updated_native_remaining_time(env):
    text = "封魂咒纹变化极慢，请在 7小时2分钟37秒 后再推演。"
    event = native_reply(OWNER, ".推演封魂咒", text, NOW + 1, root=100, command_at=NOW)
    assert asyncio.run(deliver(event))
    original_due = env.owner["wanxin_observation"]["next_deduce_time"]
    cosmetic = replace(event, event_type="edit", server_event_at=NOW + 2, text=text + "\n")
    assert asyncio.run(deliver(cosmetic))
    assert env.owner["wanxin_observation"]["next_deduce_time"] == original_due
    corrected = replace(event, event_type="edit", server_event_at=NOW + 3, text=text.replace("7小时2分钟37秒", "5分钟"))
    assert asyncio.run(deliver(corrected))
    assert env.owner["wanxin_observation"]["next_deduce_time"] == NOW + 3 + 300 + wanxin.CD_BUFFER_SEC


def test_greet_edit_updates_absolute_fields_without_repeating_gain_or_cd(env):
    env.owner["concubine_affinity"] = 120
    text = "【婉影问安】\n情缘 +9。\n婉心 115 | 魂封 0 | 月魄 38 | 咒源 120"
    event = native_reply(OWNER, ".婉影问安", text, NOW + 1, root=100, command_at=NOW)
    assert asyncio.run(deliver(event))
    due = env.owner["wanxin_observation"]["next_moon_greet_time"]
    edited = replace(event, text=text.replace("婉心 115", "婉心 116"), event_type="edit", server_event_at=NOW + 3)
    assert asyncio.run(deliver(edited))
    assert env.owner["concubine_affinity"] == 129
    assert env.owner["wanxin_observation"]["wanxin"] == 116
    assert env.owner["wanxin_observation"]["next_moon_greet_time"] == due


def test_existing_same_commission_preserves_external_claim_and_publication_clock(env):
    env.owner["wanxin_observation"]["commission"].update(
        id=5, published_at=NOW - 1000, claimed_elsewhere=True, claim_helper_username="outside", cancel_due_at=NOW + 9000,
    )
    event = native_reply(OWNER, ".发布解咒委托 1", "你已有进行中的解咒委托（ID: 5），不可重复发布。", NOW + 1, root=100)
    assert asyncio.run(deliver(event))
    commission = env.owner["wanxin_observation"]["commission"]
    assert commission["claimed_elsewhere"]
    assert commission["published_at"] == NOW - 1000
    assert commission["cancel_due_at"] == NOW + 9000


def test_same_second_old_cancel_cannot_clear_newer_publication(env):
    publication = native_reply(OWNER, ".发布解咒委托 1", "【解咒委托已发布】\n委托 ID：6", NOW, root=200, command_at=NOW)
    assert asyncio.run(deliver(publication))
    before = copy.deepcopy(env.owner)
    old_cancel = native_reply(OWNER, ".取消解咒委托", "解咒委托已取消，已退回 1 灵石。", NOW + 1, root=100, command_at=NOW)
    assert not asyncio.run(deliver(old_cancel))
    assert env.owner == before


def test_older_native_reply_cannot_rewind_newer_action(env):
    assert asyncio.run(deliver(visit_event(at=NOW + 4, root=200)))
    before = copy.deepcopy(env.owner)
    assert not asyncio.run(deliver(visit_event(at=NOW + 1)))
    assert env.owner == before


@pytest.mark.parametrize("unknown_send", [False, True])
def test_native_log_recovery_closes_exact_known_or_unknown_operation(env, unknown_send):
    env.monkeypatch.setattr(wanxin, "send_game_command", AsyncMock(return_value=None if unknown_send else receipt()))
    if unknown_send:
        env.monkeypatch.setattr(wanxin, "classify_game_send_block", lambda *_args: {"status": "unknown", "code": "send_timeout"})
    asyncio.run(schedule())
    pending = env.owner["wanxin_observation"]["pending"]
    entries = native_logs(visit_event())
    if unknown_send:
        entries.append({
            "event_type": "sent", "message_id": 100, "chat_id": CHAT, "sender_id": OWNER,
            "text": ".探望南宫婉", "op_id": pending["op_id"], "account_id": OWNER,
            "source_module": wanxin.WANXIN_MODULE_NAME,
        })
    env.monkeypatch.setattr(wanxin, "_iter_message_log_entries_between", lambda *_args: iter((row, NOW + 2) for row in entries))
    sender = AsyncMock()
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule(NOW + 200))
    sender.assert_not_awaited()
    assert not env.owner["wanxin_observation"]["pending"]
    assert env.owner["wanxin_observation"]["next_visit_time"] > NOW


def test_unresolved_timeout_never_rearms_a_mutation_or_changes_its_cd(env):
    env.owner["wanxin_observation"]["auto_config"].update(protect_enabled=False, deduce_enabled=False)
    sender = AsyncMock(return_value=receipt())
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule())
    for later in (NOW + 100, NOW + 1000, NOW + 90000):
        asyncio.run(schedule(later))
    sender.assert_awaited_once()
    observed = env.owner["wanxin_observation"]
    assert not observed["pending"]
    assert observed["unresolved_actions"]["visit"]["status"] == "unknown"
    assert observed["unresolved_actions"]["visit"]["msg_id"] == 100
    assert observed["next_visit_time"] == NOW - 1


def test_verified_expired_external_commission_can_be_cancelled(env):
    published_at = NOW - wanxin.WANXIN_COMMISSION_TTL_SEC - wanxin.CD_BUFFER_SEC - 1
    publication = native_reply(
        OWNER, ".发布解咒委托 1", "【解咒委托已发布】\n委托 ID：5", published_at,
        root=20, command_at=published_at - 1,
    )
    acceptance = native_reply(
        999001, ".接取解咒委托 5", "【咒契协定已成】\n阴罗宗弟子 @outside 已接取 @owner 的解咒委托。",
        published_at + 3, root=30, command_at=published_at + 2,
    )
    env.owner["wanxin_observation"]["commission"].update(
        id=5, published_at=published_at, publish_msg_id=20, claimed_elsewhere=True, cancel_due_at=NOW - 1,
    )
    entries = native_logs(publication, acceptance)
    env.monkeypatch.setattr(wanxin, "_iter_message_log_entries_between", lambda *_args: iter((row, row["server_event_at"]) for row in entries))
    sender = AsyncMock(return_value=receipt())
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    asyncio.run(schedule())
    sender.assert_awaited_once()
    assert sender.await_args.args[0] == ".取消解咒委托"
    event = native_reply(OWNER, ".取消解咒委托", "解咒委托已取消，已退回 1 灵石。", NOW + 1, root=100, command_at=NOW)
    assert asyncio.run(deliver(event))
    assert env.owner["wanxin_observation"]["commission"]["id"] == 0
    assert not env.owner["wanxin_observation"]["pending"]
