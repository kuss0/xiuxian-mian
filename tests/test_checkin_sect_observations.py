import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import action_guard, app, app_runtime, persistence, profile_observation, runtime
from model import state as state_module
from model.features import checkin, passive_inbox
from model.verified_event import from_telegram_event


NOW = 1780000000.0
IDENTITY = 990640001
ACCOUNT = 7641
CHAT = -100640001
OTHER_CHAT = -100640002
ROOT = 6401
BOT = 880640001
SECT = "\u661f\u5bab"
NO_SECT = "\u6563\u4fee"
DENIAL = "\u6563\u4fee\u65e0\u9700\u70b9\u536f\uff0c\u901f\u901f\u5bfb\u4e00\u5b97\u95e8\u62dc\u5165\u5427\u3002"
SWITCHES = (
    "checkin_enabled", "sect_teach_enabled", "tower_enabled", "tree_enabled",
    "ranch_enabled", "stargazer_enabled", "guanxing_enabled", "tianti_enabled",
    "taiyi_enabled", "taiyi_node_search_enabled",
)


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    state_module.set_game_group_route_config({
        "primary_group_id": CHAT, "backup_group_ids": [OTHER_CHAT], "enabled": True,
    })
    state_module.set_game_bot_ids([BOT])
    state_module.update_send_as_profile(IDENTITY, username="SectOwner", sect_name=SECT)
    identity = state_module.get_identity_state(IDENTITY)
    identity.update({key: True for key in SWITCHES})
    identity.update(
        next_checkin_time=NOW + 200, next_sect_teach_time=NOW + 200,
        next_tower_time=NOW + 200, last_sect_teach_msg_id=ROOT + 1,
        last_sect_teach_chat_id=CHAT, stargazer_followup_due_at=NOW + 300,
        stargazer_last_panel_msg_id=ROOT + 2, taiyi_phase="node_define_sent",
        taiyi_node_define_msg_id=ROOT + 3, taiyi_phase_entered_at=NOW,
    )
    for chat, root, command in (
        (CHAT, ROOT, checkin.CMD_CHECKIN),
        (OTHER_CHAT, ROOT, checkin.CMD_CHECKIN),
        (CHAT, ROOT + 1, checkin.CMD_SECT_TEACH),
        (CHAT, ROOT + 2, checkin.CMD_STARGAZER_COLLECT),
        (CHAT, ROOT + 3, checkin.CMD_NODE_DEFINE),
    ):
        identity["pending_tasks"][(chat, root)] = {
            "cmd": command, "chat_id": chat, "sent_at": NOW,
            "timeout": 60, "retry": 0, "max_retry": 1,
        }
        identity["my_msg_ids"][(chat, root)] = NOW
    for module in (app, checkin, passive_inbox):
        monkeypatch.setattr(module, "save_state", Mock(return_value=True))
    monkeypatch.setattr(checkin, "send_audit_log", AsyncMock())
    monkeypatch.setattr(checkin, "console_log", Mock())
    monkeypatch.setattr(app, "send_audit_log", AsyncMock())
    monkeypatch.setattr(app, "schedule_cleanup", AsyncMock())
    monkeypatch.setattr(app, "_early_routed_replies", {})
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    monkeypatch.setattr(action_guard, "_recent_closed_command_guards", {})
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(passive_inbox, "_observed_passive_events", {})
    monkeypatch.setattr(passive_inbox, "_passive_stats", copy.deepcopy(passive_inbox._PASSIVE_STATS_DEFAULT))
    monkeypatch.setattr(passive_inbox, "_save_passive_stats", Mock())
    monkeypatch.setattr(checkin.time, "time", lambda: NOW + 100)
    for module in (app, checkin, runtime):
        monkeypatch.setattr(module, "send_game_command", AsyncMock(side_effect=AssertionError("no game sends")))
    try:
        with state_module.use_identity(IDENTITY):
            yield identity
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(saved)


def fixture(*, at=NOW + 10, edited=False, chat=CHAT, root=ROOT):
    event = SimpleNamespace(
        id=root + 100, sender_id=BOT, chat_id=chat,
        date=datetime.fromtimestamp(NOW + 1 if edited else at, timezone.utc),
        edit_date=datetime.fromtimestamp(at, timezone.utc) if edited else None,
        reply_to=SimpleNamespace(reply_to_msg_id=root),
    )
    command = SimpleNamespace(
        id=root, raw_text=checkin.CMD_CHECKIN, sender_id=IDENTITY, chat_id=chat,
        date=datetime.fromtimestamp(NOW, timezone.utc),
    )
    context = {
        "send_as_id": IDENTITY, "family": "checkin", "chat_id": chat,
        "reply_to_msg_id": root, "root_msg_id": root, "reply_to_sender_id": IDENTITY,
        "reply_to_command": checkin.CMD_CHECKIN, "reply_to_server_at": NOW,
    }
    return event, command, context


async def deliver(path="direct", *, event=None, command=None, context=None, edited=False, at=NOW + 10, text=DENIAL):
    defaults = fixture(at=at, edited=edited)
    event, command, context = (
        value if value is not None else default
        for value, default in zip((event, command, context), defaults)
    )
    kind = "edit" if edited else "message"
    if path == "direct":
        return await app._handle_routed_reply_event(event, text, NOW + 100, command, context, event_kind=kind)
    verified = from_telegram_event(event, text, context, event_kind=kind)
    return await passive_inbox.handle_passive_module_card(verified, now=NOW + 100)


@pytest.mark.parametrize("path", ["direct", "passive"])
@pytest.mark.parametrize("edited", [False, True])
def test_old_no_sect_reply_cannot_disable_newer_membership(env, path, edited):
    assert profile_observation.apply_profile_observation(IDENTITY, {"sect_name": SECT}, NOW + 20)
    asyncio.run(deliver(path, edited=edited))
    assert state_module.get_send_as_profile(IDENTITY)["sect_name"] == SECT
    assert all(env[key] for key in SWITCHES)
    assert (CHAT, ROOT) not in env["pending_tasks"]
    assert (OTHER_CHAT, ROOT) in env["pending_tasks"]
    checkin.send_audit_log.assert_not_awaited()


@pytest.mark.parametrize("path", ["direct", "passive"])
def test_fresh_denial_keeps_other_inflight_work_and_receipts(env, path):
    before_receipts = copy.deepcopy(env["my_msg_ids"])
    before_pending = copy.deepcopy(env["pending_tasks"])
    anchors = {key: env[key] for key in (
        "last_sect_teach_msg_id", "last_sect_teach_chat_id", "stargazer_followup_due_at",
        "stargazer_last_panel_msg_id", "taiyi_phase", "taiyi_node_define_msg_id", "taiyi_phase_entered_at",
    )}
    assert asyncio.run(deliver(path))
    assert state_module.get_send_as_profile(IDENTITY)["sect_name"] == NO_SECT
    assert not any(env[key] for key in SWITCHES)
    assert env["my_msg_ids"] == before_receipts
    assert set(env["pending_tasks"]) == set(before_pending) - {(CHAT, ROOT)}
    for pending in env["pending_tasks"].values():
        assert pending["max_retry"] == 0
    assert {key: env[key] for key in anchors} == anchors
    assert env["identity_profile_observed_at"]["sect_name"] == NOW + 10


@pytest.mark.parametrize("path", ["direct", "passive"])
@pytest.mark.parametrize("fault", ["sender", "chat", "time", "future", "owner", "command", "root", "reply_sender"])
def test_no_sect_requires_official_owned_command_and_server_time(env, path, fault):
    event, command, context = fixture()
    if fault == "sender":
        event.sender_id += 1
    elif fault == "chat":
        event.chat_id = command.chat_id = context["chat_id"] = CHAT - 100
    elif fault == "time":
        event.date = None
    elif fault == "future":
        event.date = datetime.fromtimestamp(NOW + 1000, timezone.utc)
    elif fault == "owner":
        context["send_as_id"] = IDENTITY + 1
    elif fault == "command":
        command.raw_text = context["reply_to_command"] = checkin.CMD_SECT_TEACH
    elif fault == "root":
        context["root_msg_id"] += 1
    else:
        command.sender_id = context["reply_to_sender_id"] = IDENTITY + 1
    before = copy.deepcopy(env)
    asyncio.run(deliver(path, event=event, command=command, context=context))
    assert state_module.get_send_as_profile(IDENTITY)["sect_name"] == SECT
    assert all(env[key] for key in SWITCHES)
    assert env["pending_tasks"] == before["pending_tasks"]
    checkin.send_audit_log.assert_not_awaited()


def test_unproven_legacy_entry_points_cannot_disable_modules(env):
    assert not asyncio.run(checkin.handle_checkin_reply(DENIAL, NOW + 100, fixture()[1]))
    assert not asyncio.run(passive_inbox._apply_checkin_passive(DENIAL, NOW + 100, "checkin"))
    assert all(env[key] for key in SWITCHES)


@pytest.mark.parametrize("path", ["direct", "passive"])
def test_no_sect_replay_does_not_override_a_later_ui_choice(env, path):
    assert asyncio.run(deliver(path))
    env["checkin_enabled"] = True
    asyncio.run(deliver("passive" if path == "direct" else "direct"))
    assert env["checkin_enabled"]
    assert checkin.send_audit_log.await_count <= 1


def test_scheduler_disables_unavailable_sends_without_erasing_unresolved_work(env):
    state_module.update_send_as_profile(IDENTITY, sect_name=NO_SECT)
    before_pending = copy.deepcopy(env["pending_tasks"])
    before_receipts = copy.deepcopy(env["my_msg_ids"])
    asyncio.run(checkin.run_checkin_scheduler(NOW + 100))
    assert set(env["pending_tasks"]) == set(before_pending)
    assert env["my_msg_ids"] == before_receipts
    assert env["last_sect_teach_msg_id"] == ROOT + 1
    checkin.send_game_command.assert_not_awaited()


@pytest.mark.parametrize("path", ["direct", "passive"])
@pytest.mark.parametrize("order", ["newer", "older", "other_chat"])
def test_same_second_sect_observations_use_comparable_source_evidence(env, path, order):
    event, command, context = fixture()
    prior = {
        "source": "telegram", "chat_id": CHAT, "msg_id": event.id + 1, "edited": False,
    }
    if order == "newer":
        prior["msg_id"] = event.id - 1
    elif order == "other_chat":
        prior["chat_id"] = OTHER_CHAT
    assert profile_observation.apply_profile_observation(IDENTITY, {"sect_name": SECT}, NOW + 10, evidence=prior)
    asyncio.run(deliver(path, event=event, command=command, context=context))
    assert state_module.get_send_as_profile(IDENTITY)["sect_name"] == (NO_SECT if order == "newer" else SECT)
    assert env["checkin_enabled"] is (order != "newer")


@pytest.mark.parametrize("path", ["direct", "passive"])
def test_legacy_profile_timestamp_blocks_older_denial_without_inventing_clocks(env, path):
    state_module.update_send_as_profile(IDENTITY, sect_updated_at=NOW + 20)
    asyncio.run(deliver(path))
    assert state_module.get_send_as_profile(IDENTITY)["sect_name"] == SECT
    assert not env["identity_profile_observed_at"]
    assert env["checkin_enabled"]


@pytest.mark.parametrize("path", ["direct", "passive"])
@pytest.mark.parametrize("clocks", [None, [], {"sect_name": "bad"}, {"sect_name": NOW, "_evidence": []}])
def test_invalid_profile_chronology_cannot_authorize_disabling(env, path, clocks):
    env["identity_profile_observed_at"] = copy.deepcopy(clocks)
    before = copy.deepcopy(env["pending_tasks"])
    asyncio.run(deliver(path))
    assert state_module.get_send_as_profile(IDENTITY)["sect_name"] == SECT
    assert env["pending_tasks"] == before
    assert all(env[key] for key in SWITCHES)


@pytest.mark.parametrize("channel", [False, True])
def test_manual_reply_context_keeps_native_command_evidence_for_passive_dispatch(env, channel):
    event, command, _context = fixture()
    env["pending_tasks"].clear()
    env["my_msg_ids"].clear()
    if channel:
        command.sender_id = -int(f"100{IDENTITY}")
    event.get_reply_message = AsyncMock(return_value=command)

    async def scenario():
        reply, context = await app._resolve_event_reply(event)
        assert reply is command
        assert context["reply_to_command"] == checkin.CMD_CHECKIN
        assert context["reply_to_server_at"] == NOW
        return await passive_inbox.handle_passive_module_card(
            from_telegram_event(event, DENIAL, context), now=NOW + 100,
        )

    assert asyncio.run(scenario())
    assert state_module.get_send_as_profile(IDENTITY)["sect_name"] == NO_SECT


@pytest.mark.parametrize("path", ["direct", "passive"])
def test_missing_command_evidence_does_not_trust_family_alone(env, path):
    event, command, context = fixture()
    env["pending_tasks"].clear()
    command.raw_text = context["reply_to_command"] = ""
    asyncio.run(deliver(path, event=event, command=command, context=context))
    assert env["checkin_enabled"]
    assert state_module.get_send_as_profile(IDENTITY)["sect_name"] == SECT


def test_retained_pending_log_reply_uses_server_time_and_exact_chat(env):
    pending = env["pending_tasks"][(CHAT, ROOT)]
    entry = {
        "message_id": ROOT + 100, "sender_id": BOT, "chat_id": CHAT,
        "reply_to_msg_id": ROOT, "text": DENIAL, "event_type": "message",
        "ts_epoch": NOW + 100, "server_event_at": NOW + 10,
    }
    assert asyncio.run(app._replay_pending_log_replies(IDENTITY, ROOT, pending, [entry], NOW + 100))
    assert env["identity_profile_observed_at"]["sect_name"] == NOW + 10
    assert (OTHER_CHAT, ROOT) in env["pending_tasks"]
    assert (CHAT, ROOT) not in env["pending_tasks"]


def test_missing_log_server_time_keeps_pending_and_does_not_invent_freshness(env):
    pending = env["pending_tasks"][(CHAT, ROOT)]
    entry = {
        "message_id": ROOT + 100, "sender_id": BOT, "chat_id": CHAT,
        "reply_to_msg_id": ROOT, "text": DENIAL, "event_type": "message", "ts_epoch": NOW + 100,
    }
    assert not asyncio.run(app._replay_pending_log_replies(IDENTITY, ROOT, pending, [entry], NOW + 100))
    assert (CHAT, ROOT) in env["pending_tasks"]
    assert env["checkin_enabled"]


@pytest.mark.parametrize("path", ["direct", "passive"])
def test_checkin_closure_is_scoped_to_one_root_and_chat(env, path, monkeypatch):
    close = Mock(return_value=False)
    passive_close = Mock()
    monkeypatch.setattr(checkin, "close_action_guard_by_family", close)
    monkeypatch.setattr(passive_inbox, "close_action_guard_by_family", passive_close)
    assert asyncio.run(deliver(path))
    assert close.call_args.kwargs["expected_msg_id"] == ROOT
    assert close.call_args.kwargs["expected_chat_id"] == CHAT
    passive_close.assert_not_called()


@pytest.mark.parametrize("notification", ["cancel", "failure"])
def test_committed_denial_survives_notification_error_and_sqlite_reload(env, monkeypatch, tmp_path, notification):
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "sect_observations.db"))
    monkeypatch.setattr(checkin, "save_state", persistence.save_state)
    exception = asyncio.CancelledError() if notification == "cancel" else RuntimeError("notification failed")
    checkin.send_audit_log.side_effect = exception
    with pytest.raises(type(exception)):
        asyncio.run(deliver())
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    identity = state_module.get_identity_state(IDENTITY)
    assert state_module.get_send_as_profile(IDENTITY)["sect_name"] == NO_SECT
    assert identity["identity_profile_observed_at"]["sect_name"] == NOW + 10
    assert not any(identity[key] for key in SWITCHES)
    assert (CHAT, ROOT) not in identity["pending_tasks"]
    assert (OTHER_CHAT, ROOT) in identity["pending_tasks"]
    assert identity["last_sect_teach_msg_id"] == ROOT + 1
    identity["checkin_enabled"] = True
    assert not asyncio.run(deliver("passive"))
    assert identity["checkin_enabled"]
    assert checkin.send_audit_log.await_count == 1


@pytest.mark.parametrize("path", ["direct", "passive"])
def test_genuine_newer_edit_is_not_lost_to_text_only_deduplication(env, path):
    assert asyncio.run(deliver(path))
    assert profile_observation.apply_profile_observation(IDENTITY, {"sect_name": SECT}, NOW + 20)
    env["checkin_enabled"] = True
    assert asyncio.run(deliver(path, edited=True, at=NOW + 30))
    assert state_module.get_send_as_profile(IDENTITY)["sect_name"] == NO_SECT
    assert not env["checkin_enabled"]
    assert env["identity_profile_observed_at"]["sect_name"] == NOW + 30
    assert env["identity_profile_observed_at"]["_evidence"]["sect_name"]["edited"]
    assert checkin.send_audit_log.await_count == 2


@pytest.mark.parametrize("path", ["direct", "passive"])
def test_partial_then_final_edit_uses_final_server_time(env, path):
    assert not asyncio.run(deliver(path, text="\u70b9\u536f\u6b63\u5728\u5904\u7406"))
    assert (CHAT, ROOT) in env["pending_tasks"]
    assert asyncio.run(deliver(path, edited=True, at=NOW + 30))
    assert env["identity_profile_observed_at"]["sect_name"] == NOW + 30
    assert (CHAT, ROOT) not in env["pending_tasks"]


@pytest.mark.parametrize("field,value", [
    ("send_as_id", float(IDENTITY)), ("family", []), ("reply_to_sender_id", False),
    ("reply_to_server_at", "bad"), ("root_msg_id", float(ROOT)), ("chat_id", float(CHAT)),
])
def test_malformed_context_does_not_authorize_disabling_or_raise(env, field, value):
    event, command, context = fixture()
    context[field] = value
    assert not asyncio.run(checkin.handle_checkin_reply(
        DENIAL, NOW + 100, command, matched_family="checkin", event=event, reply_context=context,
    ))
    assert all(env[key] for key in SWITCHES)


@pytest.mark.parametrize("path", ["direct", "passive"])
def test_stale_reply_for_deleted_identity_cannot_create_or_change_another_role(env, path):
    other = state_module.ensure_identity_registered(IDENTITY + 1)
    other["checkin_enabled"] = True
    state_module.remove_identity(IDENTITY)
    assert not asyncio.run(deliver(path))
    assert not state_module.has_identity(IDENTITY)
    assert other["checkin_enabled"]


@pytest.mark.parametrize("path", ["direct", "passive"])
@pytest.mark.parametrize("drift", ["delete", "replace", "rebind"])
def test_notification_await_cannot_clear_replaced_owner_pending(env, path, drift):
    state_module.ensure_identity_registered(IDENTITY + 1)
    expected = {}

    async def notify(*_args, **_kwargs):
        if drift == "delete":
            state_module.remove_identity(IDENTITY)
            return
        if drift == "replace":
            state_module._meta_state["identity_states"][IDENTITY] = state_module.new_identity_state()
        else:
            state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        owner = state_module.get_identity_state(IDENTITY)
        owner["pending_tasks"][(CHAT, ROOT)] = {
            "cmd": checkin.CMD_CHECKIN, "chat_id": CHAT, "sent_at": NOW + 100,
            "retry": 0, "max_retry": 0, "timeout": 60,
        }
        expected["pending"] = copy.deepcopy(owner["pending_tasks"])

    checkin.send_audit_log.side_effect = notify
    assert asyncio.run(deliver(path))
    if drift == "delete":
        assert not state_module.has_identity(IDENTITY)
    else:
        assert state_module.get_identity_state(IDENTITY)["pending_tasks"] == expected["pending"]
