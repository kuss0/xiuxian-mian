import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, control, identity_refresh, persistence, runtime
from model import state as state_module
from model.config import CMD_IDENTITY_INFO, CMD_YUANYING_STATUS, CMD_SECOND_SOUL_STATUS
from test_passive_identity_profile import COMBINED_CARD


NOW = 1780000000.0
IDENTITY = 990620001
ACCOUNT = 7621
CHAT = -100620001
ROOT = 6201
BOT = 880620001
PARTIAL_CARD = COMBINED_CARD.split("\n\n\u3010\u65b0\u624b\u79d8\u7c4d\u3011")[0]
REMEMBER_EARLY_REPLY = app._remember_early_routed_reply


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    state_module.set_game_group_route_config({"primary_group_id": CHAT})
    state_module.set_game_bot_ids([BOT])
    state_module.update_send_as_profile(IDENTITY, username="jfdffdddd")
    identity = state_module.get_identity_state(IDENTITY)
    calls = []

    def receipt(command, kwargs, *, root=None, at=NOW, chat=CHAT, started_at=None):
        return runtime._finalize_game_command_sent(
            command, msg_id=root or ROOT + len(calls) - 1,
            sent_at=at, send_started_at=at if started_at is None else started_at, send_as_id=IDENTITY,
            game_group_id=chat, track=True, max_retry=kwargs.get("max_retry", 1),
            append_sent_log=False,
            send_intent={key: kwargs[key] for key in ("source_module", "op_id", "chain_id") if key in kwargs},
        )

    async def send(command, **kwargs):
        calls.append((command, kwargs))
        return receipt(command, kwargs)

    sender = AsyncMock(side_effect=send)
    monkeypatch.setattr(control, "send_game_command", sender)
    monkeypatch.setattr(control, "save_state", Mock(return_value=True))
    monkeypatch.setattr(control, "send_audit_log", AsyncMock())
    monkeypatch.setattr(control, "console_log", Mock())
    monkeypatch.setattr(control, "is_auto_delete_sent_messages_enabled", lambda: False)
    monkeypatch.setattr(control, "remember_second_soul_status_read", AsyncMock(return_value=True))
    monkeypatch.setattr(control.time, "time", lambda: NOW)
    monkeypatch.setattr(control.random, "randint", lambda *_: 20)
    monkeypatch.setattr(runtime, "_GAME_COMMAND_SENT_OBSERVERS", [
        observer for observer in runtime._GAME_COMMAND_SENT_OBSERVERS
        if observer.__module__ in {control.__name__, "model.identity_refresh"}
    ])
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(runtime, "_GAME_SEND_BLOCK_LAST", {})
    monkeypatch.setattr(identity_refresh, "_owners", {})
    monkeypatch.setattr(identity_refresh, "_live_calls", set())
    monkeypatch.setattr(runtime, "note_game_command_sent", Mock())
    monkeypatch.setattr(runtime, "action_guard_note_sent", Mock())
    monkeypatch.setattr(app, "schedule_cleanup", AsyncMock())
    monkeypatch.setattr(app, "_claim_runtime_event", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(app, "_has_runtime_message_consumed", lambda *_args: False)
    monkeypatch.setattr(app, "_remember_early_routed_reply", Mock())
    with state_module.use_identity(IDENTITY):
        yield SimpleNamespace(identity=identity, calls=calls, send=sender, receipt=receipt)
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


async def deliver(text=COMBINED_CARD, *, root=ROOT, chat=CHAT, at=NOW + 1,
                  command=CMD_IDENTITY_INFO, reply_id=None, sender=IDENTITY):
    event = SimpleNamespace(
        id=reply_id or root + 100, chat_id=chat, sender_id=BOT,
        date=datetime.fromtimestamp(at, timezone.utc),
    )
    reply = SimpleNamespace(
        id=root, chat_id=chat, sender_id=sender, raw_text=command,
        date=datetime.fromtimestamp(NOW, timezone.utc),
    )
    context = {
        "send_as_id": IDENTITY, "chat_id": chat, "family": "identity_info",
        "root_msg_id": root, "reply_to_msg_id": root,
    }
    return await app._handle_routed_reply_event(event, text, max(NOW + 10, at), reply, context)


def test_duplicate_click_is_pending_before_first_receipt(env):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def send(command, **kwargs):
            env.calls.append((command, kwargs))
            if len(env.calls) == 1:
                started.set()
                await release.wait()
            return env.receipt(command, kwargs)

        env.send.side_effect = send
        first = asyncio.create_task(control.refresh_identity_info(IDENTITY))
        await started.wait()
        try:
            assert control.is_identity_info_refresh_pending(IDENTITY)
            ok, _ = await control.refresh_identity_info(IDENTITY)
            assert ok
            assert [cmd for cmd, _ in env.calls] == [CMD_IDENTITY_INFO]
        finally:
            release.set()
            await first

    asyncio.run(scenario())


def test_early_complete_result_is_retained_and_extra_reads_still_run(env):
    async def send(command, **kwargs):
        env.calls.append((command, kwargs))
        msg = env.receipt(command, kwargs)
        if command == CMD_IDENTITY_INFO:
            assert await deliver()
        return msg

    env.send.side_effect = send
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert not control.is_identity_info_refresh_pending(IDENTITY)
    assert [cmd for cmd, _ in env.calls] == [CMD_IDENTITY_INFO, CMD_YUANYING_STATUS, CMD_SECOND_SOUL_STATUS]
    assert state_module.get_send_as_profile(IDENTITY)["xiuwei_current"] == 445955


@pytest.mark.parametrize("bad_anchor", ["chat", "command", "stale_time", "rebind"])
def test_reply_requires_original_request_owner_and_server_time(env, bad_anchor):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    if bad_anchor == "rebind":
        state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
    before = copy.deepcopy(state_module.get_send_as_profile(IDENTITY))
    assert not asyncio.run(deliver(
        chat=CHAT - 1 if bad_anchor == "chat" else CHAT,
        command=".unrelated" if bad_anchor == "command" else CMD_IDENTITY_INFO,
        at=NOW - 10 if bad_anchor == "stale_time" else NOW + 1,
    ))
    assert state_module.get_send_as_profile(IDENTITY) == before
    assert (CHAT, ROOT) in env.identity["pending_tasks"]


def test_completed_refresh_cleans_only_its_own_pending_roots(env):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    other = {"cmd": CMD_IDENTITY_INFO, "sent_at": NOW, "chat_id": CHAT - 1, "max_retry": 1}
    env.identity["pending_tasks"][(CHAT - 1, ROOT)] = copy.deepcopy(other)
    env.identity["my_msg_ids"][(CHAT - 1, ROOT)] = NOW
    assert asyncio.run(deliver())
    assert env.identity["pending_tasks"].get((CHAT - 1, ROOT)) == other
    assert env.identity["my_msg_ids"][(CHAT - 1, ROOT)] == NOW
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]


def test_followup_reserves_before_send_and_does_not_reenter(env):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert asyncio.run(deliver(PARTIAL_CARD))
    env.calls.clear()

    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()

        async def send(command, **kwargs):
            env.calls.append((command, kwargs))
            if len(env.calls) == 1:
                started.set()
                await release.wait()
            return env.receipt(command, kwargs, root=ROOT + 20, at=NOW + 30)

        env.send.side_effect = send
        first = asyncio.create_task(control.run_identity_info_followup_scheduler(NOW + 30))
        await started.wait()
        try:
            await control.run_identity_info_followup_scheduler(NOW + 30)
            assert len(env.calls) == 1
        finally:
            release.set()
            await first

    asyncio.run(scenario())


@pytest.mark.parametrize("invalidation", ["replace", "rebind"])
def test_followup_receipt_cannot_write_through_a_changed_owner(env, invalidation):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert asyncio.run(deliver(PARTIAL_CARD))
    captured = {}

    async def send(_command, **_kwargs):
        if invalidation == "replace":
            state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(env.identity)
        else:
            state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        captured.update(copy.deepcopy(state_module.get_identity_state(IDENTITY)))
        return SimpleNamespace(id=ROOT + 20, chat_id=CHAT, sent_at=NOW + 30)

    env.send.side_effect = send
    asyncio.run(control.run_identity_info_followup_scheduler(NOW + 30))
    assert state_module.get_identity_state(IDENTITY) == captured


def test_delete_rechecks_identity_after_rpc(env, monkeypatch):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    captured = {}

    async def delete(_chat, _ids):
        state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(env.identity)
        captured.update(copy.deepcopy(state_module.get_identity_state(IDENTITY)))

    async def rpc(coro, **_kwargs):
        return await coro

    monkeypatch.setattr(control, "is_auto_delete_sent_messages_enabled", lambda: True)
    monkeypatch.setattr(runtime, "_get_identity_client_with_account", lambda *_: (ACCOUNT, SimpleNamespace(delete_messages=delete)))
    monkeypatch.setattr(runtime, "_run_account_rpc", rpc)
    asyncio.run(control.delete_identity_info_trigger_msg(IDENTITY, ROOT))
    assert state_module.get_identity_state(IDENTITY) == captured


def test_unknown_send_does_not_allow_an_immediate_second_click(env):
    env.send.side_effect = None
    env.send.return_value = None
    assert not asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert control.is_identity_info_refresh_pending(IDENTITY)
    asyncio.run(control.refresh_identity_info(IDENTITY))
    env.send.assert_awaited_once()


def test_cancelled_send_keeps_pending_request_evidence(env):
    env.send.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(control.refresh_identity_info(IDENTITY))
    assert control.is_identity_info_refresh_pending(IDENTITY)
    assert env.identity["identity_info_last_requested_at"] == NOW


def test_zero_current_xiuwei_is_a_valid_complete_field(env):
    payload = control._normalize_identity_refresh_payload(control._parse_identity_info_partial(COMBINED_CARD))
    payload["xiuwei_current"] = 0
    assert "xiuwei" not in control._get_identity_refresh_missing_fields(payload)


def passive_event(at, *, root=0, source="", chat=CHAT, sender=BOT):
    return SimpleNamespace(
        id=ROOT + 200, chat_id=chat, sender_id=sender,
        date=datetime.fromtimestamp(at, timezone.utc),
        reply_context={
            "send_as_id": IDENTITY if root else 0, "chat_id": chat,
            "family": "identity_info" if root else "", "source": source,
            "root_msg_id": root, "reply_to_msg_id": root,
        },
    )


@pytest.mark.parametrize("bad", ["automatic_root", "other_identity", "no_time", "bot", "chat"])
def test_passive_card_cannot_bypass_reply_ownership(env, bad):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    event = passive_event(
        NOW + 1, root=ROOT if bad in {"automatic_root", "other_identity"} else 0,
        source="manual_game_command" if bad == "other_identity" else "",
        sender=BOT - 1 if bad == "bot" else BOT,
        chat=CHAT - 1 if bad == "chat" else CHAT,
    )
    if bad == "no_time":
        event.date = None
    if bad == "other_identity":
        event.reply_context["send_as_id"] = IDENTITY + 1
    before = copy.deepcopy(state_module.get_send_as_profile(IDENTITY))
    assert not asyncio.run(control.handle_passive_identity_profile_card(COMBINED_CARD, NOW + 10, event=event))
    assert state_module.get_send_as_profile(IDENTITY) == before


def test_newer_manual_card_survives_delayed_refresh_result(env):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    newer = COMBINED_CARD.replace("445955 /", "900000 /")
    assert asyncio.run(control.handle_passive_identity_profile_card(
        newer, NOW + 30, event=passive_event(NOW + 20, root=ROOT + 20, source="manual_game_command"),
    ))
    assert asyncio.run(deliver(at=NOW + 1))
    assert state_module.get_send_as_profile(IDENTITY)["xiuwei_current"] == 900000
    assert not control.is_identity_info_refresh_pending(IDENTITY)


def test_old_passive_edit_does_not_roll_back_newer_observation(env):
    newer = COMBINED_CARD.replace("445955 /", "900000 /")
    assert asyncio.run(control.handle_passive_identity_profile_card(newer, NOW + 30, event=passive_event(NOW + 20)))
    before = copy.deepcopy(state_module.get_send_as_profile(IDENTITY))
    assert asyncio.run(control.handle_passive_identity_profile_card(COMBINED_CARD, NOW + 1000, event=passive_event(NOW + 1)))
    assert state_module.get_send_as_profile(IDENTITY) == before


def configure_retry(env, monkeypatch):
    pending = env.identity["pending_tasks"][(CHAT, ROOT)]
    pending["timeout"] = 1
    for key in list(env.identity["pending_tasks"]):
        if key != (CHAT, ROOT):
            env.identity["pending_tasks"].pop(key)
    monkeypatch.setattr(runtime, "get_bot_last_seen_at", lambda: NOW + 200)
    monkeypatch.setattr(runtime, "should_pause_for_bot_health", lambda: False)
    monkeypatch.setattr(runtime, "_recover_pending_reply_from_message_log", AsyncMock(return_value=None))
    monkeypatch.setattr(runtime, "_is_pending_consumed", lambda *_: False)
    monkeypatch.setattr(runtime, "send_audit_log", AsyncMock())
    monkeypatch.setattr(persistence, "save_state", Mock(return_value=True))
    monkeypatch.setattr(runtime, "send_game_command", env.send)
    env.calls.clear()
    return pending


def test_existing_one_retry_remains_owned_and_completes(env, monkeypatch):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    configure_retry(env, monkeypatch)

    async def send(command, **kwargs):
        env.calls.append((command, kwargs))
        assert kwargs["operation_check"]()
        return env.receipt(command, kwargs, root=ROOT + 20, at=NOW + 30)

    env.send.side_effect = send
    asyncio.run(runtime.run_retry_scheduler(NOW + 30, IDENTITY))
    pending = env.identity["pending_tasks"][(CHAT, ROOT + 20)]
    assert pending["retry"] == 1
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert len(env.calls) == 1
    assert asyncio.run(deliver(root=ROOT + 20, at=NOW + 31))
    assert not control.is_identity_info_refresh_pending(IDENTITY)


def test_retry_unknown_cannot_repeat_on_the_next_scheduler_tick(env, monkeypatch):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    configure_retry(env, monkeypatch)
    env.send.side_effect = None
    env.send.return_value = None
    asyncio.run(runtime.run_retry_scheduler(NOW + 30, IDENTITY))
    asyncio.run(runtime.run_retry_scheduler(NOW + 200, IDENTITY))
    assert env.send.await_count == 4  # Three original reads, one attempted retry.
    request = identity_refresh.request_for(IDENTITY)
    assert request["commands"][-1]["status"] == "unknown"


def test_retry_cannot_send_after_original_result_completes(env, monkeypatch):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    configure_retry(env, monkeypatch)

    async def send(_command, **kwargs):
        assert kwargs["operation_check"]()
        assert await deliver()
        assert not kwargs["operation_check"]()
        return None

    env.send.side_effect = send
    asyncio.run(runtime.run_retry_scheduler(NOW + 30, IDENTITY))
    assert not control.is_identity_info_refresh_pending(IDENTITY)


def test_request_survives_temporary_sqlite_reload(env, monkeypatch, tmp_path):
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "refresh.db"))
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    request = copy.deepcopy(env.identity["identity_info_refresh"])
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    identity_refresh._owners.clear()
    assert persistence.load_state()
    assert identity_refresh.request_for(IDENTITY) == request
    assert asyncio.run(deliver())
    assert state_module.get_send_as_profile(IDENTITY)["xiuwei_current"] == 445955


@pytest.mark.parametrize("location,value", [
    ("status", []), ("version", True), ("commands", None),
    ("record_status", []), ("record_reply_at", float("nan")),
    ("record_dispatch_at", NOW + 100), ("record_payload", {"xiuwei_max": "bad"}),
])
def test_malformed_request_is_not_authority_for_send_or_reply(env, location, value):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    request = env.identity["identity_info_refresh"]
    if location.startswith("record_"):
        request["commands"][0][location.removeprefix("record_")] = value
    else:
        request[location] = value
    assert identity_refresh.request_for(IDENTITY) is None
    before_calls = env.send.await_count
    asyncio.run(control.run_identity_info_followup_scheduler(NOW + 100))
    assert not asyncio.run(deliver())
    assert env.send.await_count == before_calls


def test_partial_edit_does_not_postpone_already_due_followup(env):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert asyncio.run(deliver(PARTIAL_CARD))
    due = env.identity["identity_info_followup_due_at"]
    assert asyncio.run(deliver(PARTIAL_CARD, at=NOW + 100))
    assert env.identity["identity_info_followup_due_at"] == due


def test_retry_definitely_unsent_respects_backoff_and_keeps_one_retry(env, monkeypatch):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    pending = configure_retry(env, monkeypatch)
    env.send.side_effect = None
    env.send.return_value = None
    monkeypatch.setattr(runtime, "classify_game_send_block", lambda *_a, **_k: {"status": "unsent", "at": NOW + 30})
    asyncio.run(runtime.run_retry_scheduler(NOW + 30, IDENTITY))
    assert identity_refresh.request_for(IDENTITY)["commands"][-1]["status"] == "unsent"
    before_calls = env.send.await_count
    asyncio.run(runtime.run_retry_scheduler(NOW + 30.5, IDENTITY))
    assert env.send.await_count == before_calls
    assert pending["retry"] == 0

    async def send(command, **kwargs):
        assert kwargs["operation_check"]()
        return env.receipt(command, kwargs, root=ROOT + 20, at=NOW + 40)

    env.send.side_effect = send
    asyncio.run(runtime.run_retry_scheduler(NOW + 40, IDENTITY))
    assert env.identity["pending_tasks"][(CHAT, ROOT + 20)]["retry"] == 1
    assert len(identity_refresh.request_for(IDENTITY)["commands"]) == 2


def test_superseded_request_pending_cannot_accumulate_retries(env, monkeypatch):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    old = env.identity["pending_tasks"][(CHAT, ROOT)]
    monkeypatch.setattr(control.time, "time", lambda: NOW + 400)
    env.send.return_value = SimpleNamespace(id=ROOT + 30, chat_id=CHAT, sent_at=NOW + 400)
    env.send.side_effect = None
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert old["max_retry"] == 0
    before = copy.deepcopy(env.identity["identity_info_refresh"])
    assert not asyncio.run(deliver())
    assert env.identity["identity_info_refresh"] == before


def test_followup_does_not_retimestamp_primary_fields(env):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert asyncio.run(deliver(PARTIAL_CARD))
    newer = COMBINED_CARD.replace("445955 /", "900000 /")
    assert asyncio.run(control.handle_passive_identity_profile_card(newer, NOW + 30, event=passive_event(NOW + 20)))
    env.send.side_effect = None
    env.send.return_value = SimpleNamespace(id=ROOT + 20, chat_id=CHAT, sent_at=NOW + 30)
    asyncio.run(control.run_identity_info_followup_scheduler(NOW + 30))
    battle = "\U0001f4ca" + COMBINED_CARD.split("\U0001f4ca", 1)[1]
    assert asyncio.run(deliver(battle, root=ROOT + 20, at=NOW + 31, command=control.format_battle_power_command()))
    assert state_module.get_send_as_profile(IDENTITY)["xiuwei_current"] == 900000


def test_stale_reply_does_not_append_tracking_or_change_clocks(env):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert asyncio.run(deliver(PARTIAL_CARD, at=NOW + 5))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(PARTIAL_CARD, at=NOW + 1, reply_id=ROOT + 120))
    assert env.identity == before


@pytest.mark.parametrize("passive_first", [False, True])
def test_same_second_refresh_result_uses_recorded_reply_order(env, passive_first):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    newer = COMBINED_CARD.replace("445955", "900000")
    event = passive_event(NOW + 20)
    event.id = ROOT + 101

    async def observe_newer():
        assert await control.handle_passive_identity_profile_card(newer, NOW + 30, event=event)

    async def scenario():
        if passive_first:
            await observe_newer()
        assert await deliver(at=NOW + 20, reply_id=ROOT + 100)
        if not passive_first:
            await observe_newer()

    asyncio.run(scenario())
    record = identity_refresh.request_for(IDENTITY)["commands"][0]
    assert record["profile_evidence"]["msg_id"] == ROOT + 100
    assert record["profile_evidence"]["chat_id"] == CHAT
    assert state_module.get_send_as_profile(IDENTITY)["xiuwei_current"] == 900000


def test_same_second_older_card_cannot_replace_request_payload_or_tracking(env):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    newer = PARTIAL_CARD.replace("445955", "900000")
    assert asyncio.run(deliver(newer, at=NOW + 20, reply_id=ROOT + 102))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(deliver(PARTIAL_CARD, at=NOW + 20, reply_id=ROOT + 101))
    assert env.identity == before


def test_same_second_native_edit_completes_partial_profile(env):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert asyncio.run(deliver(PARTIAL_CARD, at=NOW + 20, reply_id=ROOT + 102))
    event = SimpleNamespace(
        id=ROOT + 102, chat_id=CHAT, sender_id=BOT,
        date=datetime.fromtimestamp(NOW + 20, timezone.utc),
        edit_date=datetime.fromtimestamp(NOW + 20, timezone.utc),
    )
    reply = SimpleNamespace(id=ROOT, chat_id=CHAT, sender_id=IDENTITY, raw_text=CMD_IDENTITY_INFO)
    context = {"send_as_id": IDENTITY, "chat_id": CHAT, "family": "identity_info", "root_msg_id": ROOT, "reply_to_msg_id": ROOT}
    assert asyncio.run(app._handle_routed_reply_event(event, COMBINED_CARD, NOW + 20, reply, context, event_kind="edit"))
    assert identity_refresh.request_for(IDENTITY)["status"] == "complete"
    assert state_module.get_send_as_profile(IDENTITY)["xiuwei_current"] == 445955


def test_unparsed_same_second_packets_do_not_evict_last_profile_evidence(env):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert asyncio.run(deliver(PARTIAL_CARD, at=NOW + 20, reply_id=ROOT + 100))
    for offset in range(1, 12):
        assert not asyncio.run(deliver("unrelated", at=NOW + 20, reply_id=ROOT + 100 + offset))
    request = identity_refresh.request_for(IDENTITY)
    assert request is not None
    assert request["commands"][0]["profile_evidence"]["msg_id"] in request["commands"][0]["reply_ids"]


def test_identical_later_card_advances_order_without_repeating_notification(env):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert asyncio.run(deliver(at=NOW + 20, reply_id=ROOT + 100))
    assert asyncio.run(deliver(at=NOW + 20, reply_id=ROOT + 102))
    before = copy.deepcopy(env.identity)
    older = COMBINED_CARD.replace("445955", "100000")
    assert not asyncio.run(deliver(older, at=NOW + 20, reply_id=ROOT + 101))
    assert env.identity == before
    assert identity_refresh.request_for(IDENTITY)["commands"][0]["profile_evidence"]["msg_id"] == ROOT + 102
    control.send_audit_log.assert_awaited_once()


def test_completed_request_and_profile_clocks_reload_idempotently(env, monkeypatch, tmp_path):
    monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "completed_refresh.db"))
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert asyncio.run(deliver())
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    identity_refresh._owners.clear()
    assert persistence.load_state()
    before = copy.deepcopy(state_module.get_identity_state(IDENTITY))
    before_profile = copy.deepcopy(state_module.get_send_as_profile(IDENTITY))
    assert asyncio.run(deliver())
    assert state_module.get_identity_state(IDENTITY) == before
    assert state_module.get_send_as_profile(IDENTITY) == before_profile


def test_real_early_reply_replays_only_after_exact_transport_receipt(env, monkeypatch):
    monkeypatch.setattr(app, "_remember_early_routed_reply", REMEMBER_EARLY_REPLY)
    monkeypatch.setattr(app, "_early_routed_replies", {})
    monkeypatch.setattr(app, "_EARLY_ROUTED_REPLY_REPLAY_DELAY_SEC", 0)
    monkeypatch.setattr(runtime, "_GAME_COMMAND_SENT_OBSERVERS", [
        identity_refresh.observe_sent, app._observe_sent_for_early_reply_replay,
    ])
    tasks = []
    monkeypatch.setattr(app, "_fire_and_forget", lambda coro: tasks.append(asyncio.create_task(coro)))

    async def send(command, **kwargs):
        env.calls.append((command, kwargs))
        if command == CMD_IDENTITY_INFO:
            event = SimpleNamespace(id=ROOT + 100, chat_id=CHAT, sender_id=BOT, date=datetime.fromtimestamp(NOW + 1, timezone.utc))
            reply = SimpleNamespace(id=ROOT, chat_id=CHAT, sender_id=IDENTITY, raw_text=command)
            context = {
                "send_as_id": IDENTITY, "chat_id": CHAT, "family": "identity_info",
                "root_msg_id": ROOT, "reply_to_msg_id": ROOT, "matched_via": "reply_sender",
            }
            assert not await app._handle_routed_reply_event(event, COMBINED_CARD, NOW + 1, reply, context)
            assert state_module.get_send_as_profile(IDENTITY).get("xiuwei_current", 0) == 0
            return env.receipt(command, kwargs, at=NOW + 5, started_at=NOW)
        return env.receipt(command, kwargs)

    async def scenario():
        env.send.side_effect = send
        assert (await control.refresh_identity_info(IDENTITY))[0]
        assert len(tasks) == 1
        await asyncio.gather(*tasks)

    asyncio.run(scenario())
    assert not control.is_identity_info_refresh_pending(IDENTITY)
    assert state_module.get_send_as_profile(IDENTITY)["xiuwei_current"] == 445955


def test_cancelled_read_adopts_late_exact_receipt_without_another_send(env):
    env.send.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(control.refresh_identity_info(IDENTITY))
    request = identity_refresh.request_for(IDENTITY)
    record = request["commands"][0]
    env.receipt(CMD_IDENTITY_INFO, {
        "op_id": record["op_id"], "chain_id": request["id"], "source_module": identity_refresh.SOURCE,
    }, root=ROOT, at=NOW + 10, started_at=NOW)
    assert asyncio.run(deliver(at=NOW + 1))
    assert not control.is_identity_info_refresh_pending(IDENTITY)
    env.send.assert_awaited_once()


def test_retry_limit_times_out_only_this_request(env, monkeypatch):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    configure_retry(env, monkeypatch)
    other = {"cmd": CMD_IDENTITY_INFO, "sent_at": NOW, "timeout": 3600, "chat_id": CHAT - 1, "max_retry": 1}
    env.identity["pending_tasks"][(CHAT - 1, ROOT)] = copy.deepcopy(other)

    async def send(command, **kwargs):
        return env.receipt(command, kwargs, root=ROOT + 20, at=NOW + 30)

    env.send.side_effect = send
    asyncio.run(runtime.run_retry_scheduler(NOW + 30, IDENTITY))
    before_calls = env.send.await_count
    asyncio.run(runtime.run_retry_scheduler(NOW + 40, IDENTITY))
    assert env.send.await_count == before_calls
    assert not control.is_identity_info_refresh_pending(IDENTITY)
    assert control.get_identity_info_refresh_error(IDENTITY)
    assert env.identity["pending_tasks"][(CHAT - 1, ROOT)] == other


def test_notification_failure_keeps_confirmed_profile_and_closes_request(env, monkeypatch):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    monkeypatch.setattr(control, "send_audit_log", AsyncMock(side_effect=RuntimeError("offline notifier")))
    assert asyncio.run(deliver())
    assert not control.is_identity_info_refresh_pending(IDENTITY)
    assert state_module.get_send_as_profile(IDENTITY)["xiuwei_current"] == 445955
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]


def test_ui_reservation_save_failure_stops_before_send(env, monkeypatch):
    monkeypatch.setattr(control, "save_state", Mock(return_value=False))
    assert not asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    env.send.assert_not_awaited()
    assert identity_refresh.request_for(IDENTITY)["commands"][0]["status"] == "unsent"


def test_legacy_unowned_followup_waits_for_explicit_refresh(env):
    env.identity.update(
        identity_info_followup_due_at=NOW, last_identity_info_msg_id=ROOT,
        identity_info_reply_msg_ids=[ROOT], identity_info_last_requested_at=NOW - 20,
    )
    before = copy.deepcopy(env.identity)
    asyncio.run(control.run_identity_info_followup_scheduler(NOW + 1000))
    assert not asyncio.run(deliver())
    env.send.assert_not_awaited()
    assert env.identity == before


@pytest.mark.parametrize("clocks", [None, [], {"xiuwei": float("nan")}, {"xiuwei": "bad"}, {"unbounded_extra_key": NOW}])
def test_corrupt_profile_clocks_cannot_report_success_or_overwrite_profile(env, clocks):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    env.identity["identity_profile_observed_at"] = clocks
    before = copy.deepcopy(state_module.get_send_as_profile(IDENTITY))
    assert asyncio.run(deliver())
    assert state_module.get_send_as_profile(IDENTITY) == before
    assert control.get_identity_info_refresh_error(IDENTITY)
    assert identity_refresh.request_for(IDENTITY)["status"] == "failed"


@pytest.mark.parametrize("sender,expected", [(IDENTITY + 1, False), (-IDENTITY, True), (-int(f"100{IDENTITY}"), True)])
def test_direct_reply_checks_native_command_sender(env, sender, expected):
    assert asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert asyncio.run(deliver(sender=sender)) is expected


@pytest.mark.parametrize("field", ["chat_id", "requested_at", "command_started_at"])
def test_queued_read_rejects_in_place_request_rewrites(env, field):
    captured = {}

    async def send(_command, **kwargs):
        assert kwargs["operation_check"]()
        request = env.identity["identity_info_refresh"]
        if field == "chat_id":
            request[field] = CHAT - 1
        elif field == "requested_at":
            request[field] = NOW - 100
        else:
            request["commands"][0]["started_at"] = NOW + 10
        captured.update(copy.deepcopy(env.identity))
        assert not kwargs["operation_check"]()
        return None

    env.send.side_effect = send
    assert not asyncio.run(control.refresh_identity_info(IDENTITY))[0]
    assert env.identity == captured
    env.send.assert_awaited_once()
