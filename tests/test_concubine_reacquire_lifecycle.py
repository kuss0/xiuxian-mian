import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, message_log_recovery, persistence, runtime, state as state_module
from model.features import concubine, passive_inbox
from tests import test_concubine_fragment_actions as fragment_tests
from tests.test_concubine_fragment_actions import ACCOUNT, BOT, CHAT, ID, NOW, ROOT


base_env = fragment_tests.env
FIELD = "concubine_reacquire_action"
SOURCE = "concubine_reacquire"
NAME = "\u82e5\u5170"
ABSENT = "\u4f60\u5c1a\u65e0\u7ea2\u989c\u77e5\u5df1\u3002"
SECT_SUCCESS = f"\u65b0\u7684\u9053\u5fc3\u4f8d\u59be\u3010{NAME}\u3011\u5df2\u88ab\u6307\u6d3e\u7ed9\u4f60\u3002"
ROMANCE_SUCCESS = f"\u4f60\u9047\u5230\u4e00\u4f4d\u540d\u4e3a\u3010{NAME}\u3011\u7684\u5973\u5b50\uff0c\u5979\u613f\u610f\u6210\u4e3a\u4f60\u7684\u4f8d\u59be\u3002"
PROGRESS = "\u4f60\u5f00\u542f\u4e86\u4e00\u6bb5\u5bfb\u7f18\u4e4b\u65c5\uff0c\u8bf7\u7a0d\u5019\u3002"
COOLDOWN = "@fixture \u795e\u5ff5\u6d88\u8017\u8fc7\u5267\uff0c\u8bf7\u5728 7\u5c0f\u65f63\u5206\u949f22\u79d2 \u540e\u518d\u8bd5\u3002"


@pytest.fixture
def env(base_env, monkeypatch):
    base_env.identity.update(
        concubine_enabled=True, concubine_auto_reacquire=True,
        concubine_tianji_enabled=False, concubine_heart_enabled=False, concubine_voyage_enabled=False,
        concubine_availability="unknown", concubine_name="", concubine_kind="",
        concubine_last_snapshot_at=0,
    )
    state_module.update_send_as_profile(ID, sect_name="\u661f\u5bab")
    monkeypatch.setattr(concubine, "_fire_and_forget", lambda coroutine: coroutine.close())
    monkeypatch.setattr(concubine.random, "uniform", lambda low, high: low)
    if hasattr(concubine, "reacquire_actions"):
        monkeypatch.setattr(concubine.reacquire_actions, "_INFLIGHT", {})
    base_env.clock[0] = NOW - 10
    base_env.send.return_value = SimpleNamespace(id=ROOT - 100, chat_id=CHAT,
                                                sent_at=NOW - 10, send_started_at=NOW - 10)
    with state_module.use_identity(ID):
        assert asyncio.run(concubine._send_status_query("status", NOW - 10))
        assert asyncio.run(concubine.handle_concubine_status_reply(
            ABSENT, NOW - 9, SimpleNamespace(id=ROOT - 100, chat_id=CHAT,
                                            raw_text=concubine.CMD_CONCUBINE_STATUS),
            current_msg_id=ROOT - 99, current_chat_id=CHAT, observed_at=NOW - 9,
            reply_context={"sender_id": BOT}))
    assert base_env.identity["concubine_availability"] == "no_partner"
    base_env.identity["next_concubine_time"] = NOW
    base_env.clock[0] = NOW
    base_env.send.return_value = SimpleNamespace(id=ROOT, chat_id=CHAT,
                                                sent_at=NOW + .5, send_started_at=NOW)
    base_env.send.reset_mock()
    base_env.save.reset_mock()
    return base_env


async def start(env):
    with state_module.use_identity(ID):
        return await concubine._send_reacquire_command(env.clock[0])


async def reply(env, text=SECT_SUCCESS, *, route="native", root=ROOT, msg_id=ROOT + 1,
                at=NOW + 1, chat=CHAT, context=None, event_type="message"):
    command = env.identity.get(FIELD, {}).get("command", concubine.CMD_CONCUBINE_SECT_MARRY)
    context = context if context is not None else {
        "send_as_id": ID, "account_id": ACCOUNT, "chat_id": chat, "family": SOURCE,
        "root_msg_id": root, "reply_to_msg_id": root, "sender_id": BOT,
    }
    event = SimpleNamespace(id=msg_id, chat_id=chat, sender_id=BOT, server_event_at=at)
    parent = SimpleNamespace(id=root, chat_id=chat, sender_id=ID, raw_text=command)
    if route == "native":
        return await app._handle_routed_reply_event(
            event, text, max(env.clock[0], at) + 1, parent, context, event_kind=event_type)
    if route == "passive":
        return await passive_inbox.handle_passive_module_card(
            text, now=max(env.clock[0], at) + 1, reply_context=context, event=event, event_type=event_type)
    with state_module.use_identity(ID):
        return await concubine.handle_concubine_reacquire_reply(
            text, max(env.clock[0], at) + 1, parent, matched_family=SOURCE,
            current_msg_id=msg_id, current_chat_id=chat, observed_at=at,
            reply_context=dict(context, event_type=event_type))


def receipt(env):
    value = env.identity[FIELD]
    with state_module.use_identity(ID):
        return runtime._finalize_game_command_sent(
            value["command"], msg_id=ROOT, sent_at=env.clock[0] + .5, send_started_at=env.clock[0],
            send_as_id=ID, game_group_id=CHAT, topic_id=0, track=True, max_retry=0,
            append_sent_log=False, send_intent={"op_id": value["op_id"], "source_module": SOURCE})


async def tick(env):
    with state_module.use_identity(ID):
        return await concubine.run_concubine_scheduler(env.clock[0])


def test_reacquire_persists_intent_and_tracks_without_retries(env):
    async def sent(command, **kwargs):
        value = env.identity[FIELD]
        assert value["status"] == "sending" and value["msg_id"] == 0
        assert command == value["command"] == concubine.CMD_CONCUBINE_SECT_MARRY
        assert (value["identity_id"], value["account_id"], value["chat_id"]) == (ID, ACCOUNT, CHAT)
        assert kwargs["track"] and kwargs["max_retry"] == 0
        assert kwargs["op_id"] == value["op_id"] and kwargs["operation_check"]()
        assert kwargs["target_chat_id"] == CHAT
        env.save.assert_called()
        return env.send.return_value

    env.send.side_effect = sent
    assert asyncio.run(start(env))


def test_unknown_reacquire_cannot_be_resent_or_reset_on_restart(env):
    env.send.return_value = None
    assert not asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        concubine.restore_concubine_runtime(NOW + 86400)
    assert env.identity == before
    env.clock[0] += 86400
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()
    assert env.identity[FIELD]["status"] == "unknown"
    assert env.identity["concubine_reacquire_blocked_until"] == 0


@pytest.mark.parametrize("switch", ["global", "identity", "module", "auto"])
def test_reacquire_entrypoint_obeys_all_controls(env, switch):
    if switch == "global":
        state_module.set_global_enabled(False)
    elif switch == "identity":
        state_module.set_identity_enabled(ID, False)
    else:
        env.identity["concubine_enabled" if switch == "module" else "concubine_auto_reacquire"] = False
    assert not asyncio.run(start(env))
    env.send.assert_not_awaited()


@pytest.mark.parametrize("change", ["delete", "replace", "rebind"])
def test_late_reacquire_transport_cannot_mutate_replacement_owner(env, change):
    after = {}

    async def sent(*_args, **_kwargs):
        if change == "delete":
            state_module.remove_identity(ID)
        elif change == "replace":
            state_module._meta_state["identity_states"][ID] = copy.deepcopy(env.identity)
        else:
            state_module.set_identity_account(ID, ACCOUNT + 1)
        after.update(copy.deepcopy(state_module._meta_state))
        return env.send.return_value

    env.send.side_effect = sent
    assert not asyncio.run(start(env))
    assert state_module._meta_state == after


@pytest.mark.parametrize("route", ["native", "passive"])
def test_scalar_only_reacquire_success_has_no_projection_authority(env, route):
    env.identity.update(concubine_phase="reacquire_pending", concubine_reacquire_msg_id=ROOT)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, route=route))
    assert env.identity == before


def test_unknown_reacquire_reply_cannot_close_pending_or_backoff(env):
    assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, "unrecognized fixture reply"))
    assert env.identity == before


@pytest.mark.parametrize("route", ["native", "passive", "direct"])
def test_romance_ack_retains_pending_and_final_edit_settles_once(env, route):
    state_module.update_send_as_profile(ID, sect_name="\u9634\u7f57\u5b97")
    assert asyncio.run(start(env))
    receipt(env)
    assert asyncio.run(reply(env, PROGRESS, route=route))
    assert env.identity[FIELD]["status"] == "sent"
    assert (CHAT, ROOT) in env.identity["pending_tasks"]
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, PROGRESS, route=route))
    assert env.identity == before
    assert asyncio.run(reply(env, ROMANCE_SUCCESS, route=route, at=NOW + 10, event_type="edit"))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_name"] == NAME
    assert env.identity["concubine_availability"] == "unknown"
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, ROMANCE_SUCCESS, route=route, at=NOW + 10, event_type="edit"))
    assert env.identity == before


def test_completed_reacquire_survives_temporary_sqlite_reload(env):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, COOLDOWN))
    assert persistence.save_state() and persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    env.identity["concubine_reacquire_blocked_until"] = 0
    env.identity["next_concubine_time"] = NOW
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("mode", ["exception", "cancel"])
def test_failed_or_cancelled_reacquire_dispatch_keeps_unknown_intent(env, mode):
    env.send.side_effect = OSError("fixture") if mode == "exception" else asyncio.CancelledError()
    with pytest.raises(OSError if mode == "exception" else asyncio.CancelledError):
        asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unknown"
    assert env.identity["concubine_reacquire_blocked_until"] == 0
    env.send.side_effect = None
    env.clock[0] += 86400
    asyncio.run(tick(env))
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("route", ["native", "passive", "direct"])
@pytest.mark.parametrize("outcome", ["progress", "terminal"])
@pytest.mark.parametrize("mode", ["return", "exception", "cancel"])
def test_early_reacquire_reply_survives_late_transport_and_duplicate_receipt(env, route, outcome, mode):
    state_module.update_send_as_profile(ID, sect_name="other")

    async def sent(*_args, **_kwargs):
        message = receipt(env)
        assert await reply(env, PROGRESS if outcome == "progress" else ROMANCE_SUCCESS, route=route)
        receipt(env)
        if mode == "exception":
            raise OSError("fixture after reply")
        if mode == "cancel":
            raise asyncio.CancelledError()
        return message

    env.send.side_effect = sent
    if mode == "return":
        assert asyncio.run(start(env))
    else:
        with pytest.raises(OSError if mode == "exception" else asyncio.CancelledError):
            asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == ("complete" if outcome == "terminal" else "sent")
    assert ((CHAT, ROOT) in env.identity["pending_tasks"]) is (outcome == "progress")
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("pause", ["global", "identity", "module", "auto"])
@pytest.mark.parametrize("early", [False, True])
def test_paused_acquisition_still_records_result_and_releases_only_its_old_phase(env, pause, early):
    def disable():
        if pause == "global":
            state_module.set_global_enabled(False)
        elif pause == "identity":
            state_module.set_identity_enabled(ID, False)
        else:
            env.identity["concubine_enabled" if pause == "module" else "concubine_auto_reacquire"] = False

    async def sent(*_args, **_kwargs):
        message = receipt(env)
        disable()
        assert await reply(env)
        return message

    if early:
        env.send.side_effect = sent
        assert asyncio.run(start(env))
    else:
        assert asyncio.run(start(env))
        disable()
        assert asyncio.run(reply(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity[FIELD]["result"]["applied"]
    assert env.identity["concubine_name"] == NAME
    assert env.identity["concubine_phase"] == "idle"
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("change", ["global", "identity", "module", "auto", "chat", "snapshot", "partner", "timer",
                                    "query", "heart", "divination", "voyage", "fragment", "gift"])
def test_queued_reacquire_revalidates_controls_plan_and_sibling_work(env, monkeypatch, change):
    block = {"status": "none"}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(_command, **kwargs):
        assert kwargs["operation_check"]()
        if change == "global":
            state_module.set_global_enabled(False)
        elif change == "identity":
            state_module.set_identity_enabled(ID, False)
        elif change == "chat":
            state_module.set_game_group_id(CHAT - 1)
        else:
            key, value = {
                "module": ("concubine_enabled", False), "auto": ("concubine_auto_reacquire", False),
                "snapshot": ("concubine_last_snapshot_at", NOW + 1), "partner": ("concubine_name", "replacement"),
                "timer": ("next_concubine_time", NOW + 99999),
                "query": ("concubine_status_query", {"invalid": True}), "heart": ("concubine_heart_session", {"invalid": True}),
                "divination": ("concubine_tianji_action", {"invalid": True}), "voyage": ("concubine_voyage_actions", {"invalid": True}),
                "fragment": ("concubine_fragment_actions", {"invalid": True}), "gift": ("concubine_gift_actions", {"invalid": True}),
            }[change]
            env.identity[key] = value
        assert not kwargs["operation_check"]()
        block.update(status="unsent", code="pre_send_guard", at=NOW)
        return None

    env.send.side_effect = sent
    assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "unsent"
    assert env.identity["concubine_reacquire_blocked_until"] == 0


@pytest.mark.parametrize("fresh", [False, True])
def test_only_fresh_definitely_unsent_reacquire_evidence_can_rearm(env, monkeypatch, fresh):
    block = {"status": "unsent", "code": "pre_send_guard", "at": NOW - 5}
    monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

    async def sent(*_args, **_kwargs):
        if fresh:
            block["at"] = NOW
        return None

    env.send.side_effect = sent
    assert not asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == ("unsent" if fresh else "unknown")
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(start(env))
    assert env.identity == before
    env.send.assert_awaited_once()


@pytest.mark.parametrize("field,bad", [
    ("op_id", "bad"), ("kind", []), ("command", []), ("msg_id", True), ("identity_id", ID + 1),
    ("account_id", True), ("chat_id", 0), ("partner_key", "bad"), ("plan_key", "bad"),
    ("status", []), ("snapshot_at", NOW + 2), ("attempts", True), ("redirects", 2), ("ack", []),
    ("sent_at", NOW - 5), ("dispatch_at", "bad"),
])
def test_malformed_reacquire_record_fails_closed_and_survives_startup(env, field, bad):
    assert asyncio.run(start(env))
    env.identity[FIELD][field] = bad
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert concubine.reacquire_actions.record() is None
        assert concubine.reacquire_actions.block_reason() == "invalid"
        concubine.restore_concubine_runtime(NOW + 3600)
    assert not asyncio.run(start(env))
    assert env.identity == before
    env.send.assert_awaited_once()


@pytest.mark.parametrize("field,bad", [
    ("sender_id", BOT + 1), ("sender_id", str(BOT)), ("send_as_id", ID + 1), ("account_id", ACCOUNT + 1),
    ("chat_id", CHAT - 1), ("root_msg_id", ROOT + 1), ("reply_to_msg_id", ROOT + 1),
    ("source_module", "foreign"), ("family", "concubine_heart"), ("op_id", "f" * 32),
    ("reply_to_command", concubine.CMD_CONCUBINE_ROMANCE), ("reply_to_command_edited", True),
])
def test_conflicting_reacquire_route_metadata_cannot_complete(env, field, bad):
    assert asyncio.run(start(env))
    context = {"send_as_id": ID, "account_id": ACCOUNT, "chat_id": CHAT, "root_msg_id": ROOT,
               "reply_to_msg_id": ROOT, "sender_id": BOT, "family": SOURCE}
    context[field] = bad
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, route="direct", context=context))
    assert env.identity == before


@pytest.mark.parametrize("text", ["", "unknown", SECT_SUCCESS + "\n" + SECT_SUCCESS,
                                  SECT_SUCCESS + "\n" + COOLDOWN, ROMANCE_SUCCESS, PROGRESS,
                                  "\u795e\u5ff5\u6d88\u8017\u8fc7\u5267\uff0c\u7a0d\u540e\u518d\u8bd5\u3002",
                                  SECT_SUCCESS.replace(NAME, "a\nb"), SECT_SUCCESS.replace(NAME, "x" * 121),
                                  SECT_SUCCESS.replace(NAME, " " + NAME)])
def test_incomplete_conflicting_or_wrong_command_result_retains_reacquire(env, text):
    assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, text))
    assert env.identity == before


@pytest.mark.parametrize("field,value", [("concubine_name", "replacement"), ("concubine_affinity", 350),
                                       ("concubine_last_snapshot_at", NOW + 5),
                                       ("concubine_name", "\u5357\u5bab\u5a49")])
def test_late_acquisition_does_not_overwrite_new_partner_projection(env, field, value):
    assert asyncio.run(start(env))
    env.identity[field] = value
    before = copy.deepcopy(env.identity)
    assert asyncio.run(reply(env))
    assert env.identity[field] == before[field]
    assert not env.identity[FIELD]["result"]["applied"]
    assert env.identity[FIELD]["status"] == "complete"


@pytest.mark.parametrize("stage", ["intent", "receipt", "ack", "terminal", "replay"])
@pytest.mark.parametrize("mode", ["false", "exception"])
def test_reacquire_save_failure_is_atomic_and_replayable(env, stage, mode):
    if stage == "ack":
        state_module.update_send_as_profile(ID, sect_name="other")
    if stage in {"ack", "terminal", "replay"}:
        assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    sends = env.send.await_count
    failure = False if mode == "false" else OSError("save fixture")
    if stage == "receipt":
        env.save.side_effect = [True, failure]
    elif mode == "false":
        env.save.return_value = False
    else:
        env.save.side_effect = failure
    action = reply(env, PROGRESS if stage == "ack" else SECT_SUCCESS) if stage in {"ack", "terminal"} else tick(env) if stage == "replay" else start(env)
    if mode == "exception":
        with pytest.raises(OSError):
            asyncio.run(action)
    else:
        asyncio.run(action)
    if stage == "receipt":
        assert env.identity[FIELD]["status"] == "sending"
    else:
        assert env.identity == before
    assert env.send.await_count == sends + (stage == "receipt")
    env.save.side_effect, env.save.return_value = None, True
    if stage in {"ack", "terminal"}:
        assert asyncio.run(reply(env, PROGRESS if stage == "ack" else SECT_SUCCESS))


@pytest.mark.parametrize("name", ["\u5357\u5bab\u5a49", "\u5357\u5bab\u5a49\u00b7\u6708\u5f71"])
def test_permanent_moon_partner_is_never_reacquired(env, name):
    env.identity["concubine_name"] = name
    assert not asyncio.run(start(env))
    env.send.assert_not_awaited()


def test_stale_no_partner_snapshot_gets_a_read_not_an_acquisition(env):
    env.clock[0] += concubine.CONCUBINE_PANEL_REUSE_MAX_AGE_SEC + 1
    env.send.return_value = SimpleNamespace(id=ROOT, chat_id=CHAT,
                                           sent_at=env.clock[0], send_started_at=env.clock[0])
    assert not asyncio.run(start(env))
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_STATUS
    assert not env.identity[FIELD]


def test_native_sent_log_and_sqlite_reload_recover_unknown_acquisition(env, monkeypatch, tmp_path):
    env.send.return_value = None
    assert not asyncio.run(start(env))
    value = env.identity[FIELD]
    stamp = lambda at: datetime.fromtimestamp(at, concubine.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8")
    entries = [
        {"event_type": "sent", "ts": stamp(NOW), "message_id": ROOT, "chat_id": CHAT, "sender_id": ID,
         "source_module": SOURCE, "op_id": value["op_id"], "account_id": ACCOUNT, "text": value["command"]},
        {"event_type": "message", "ts": stamp(NOW + 1), "message_id": ROOT + 1, "chat_id": CHAT,
         "sender_id": BOT, "sender_is_bot": True, "reply_to_msg_id": ROOT, "server_event_at": NOW + 1, "text": SECT_SUCCESS},
    ]
    log = tmp_path / (datetime.fromtimestamp(NOW, concubine.TZ_LOCAL).date().isoformat() + ".log")
    log.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")
    monkeypatch.setattr(message_log_recovery, "MESSAGES_DIR", str(tmp_path))
    monkeypatch.setattr(concubine, "find_recent_message_log_commands", message_log_recovery.find_recent_message_log_commands)
    monkeypatch.setattr(concubine, "find_message_log_replies", message_log_recovery.find_message_log_replies)
    assert persistence.save_state() and persistence.load_state()
    env.identity = state_module.get_identity_state(ID)
    env.clock[0] += 3
    asyncio.run(tick(env))
    assert env.identity[FIELD]["status"] == "complete"
    assert env.identity["concubine_name"] == NAME
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine.reacquire_actions.recover(env.clock[0]))
    assert env.identity == before
    env.send.assert_awaited_once()


@pytest.mark.parametrize("route", ["native", "passive", "direct"])
def test_romance_same_clock_prompt_edit_cannot_choose_a_terminal_outcome(env, route):
    state_module.update_send_as_profile(ID, sect_name="other")
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, PROGRESS, route=route))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, ROMANCE_SUCCESS, route=route, event_type="edit"))
    assert env.identity == before
    assert asyncio.run(reply(env, ROMANCE_SUCCESS, route=route, event_type="edit", at=NOW + 2))


def test_later_message_in_same_second_can_complete_romance(env):
    state_module.update_send_as_profile(ID, sect_name="other")
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, PROGRESS))
    assert asyncio.run(reply(env, ROMANCE_SUCCESS, msg_id=ROOT + 2))
    assert env.identity[FIELD]["status"] == "complete"


@pytest.mark.parametrize("at", [0, True, "1700000501", float("nan"), float("inf"), NOW - 2, NOW + 3])
def test_reacquire_rejects_missing_malformed_old_or_future_event_clock(env, at):
    assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine.handle_concubine_reacquire_reply(
            SECT_SUCCESS, NOW + 2,
            SimpleNamespace(id=ROOT, chat_id=CHAT, raw_text=concubine.CMD_CONCUBINE_SECT_MARRY),
            current_msg_id=ROOT + 1, current_chat_id=CHAT, observed_at=at,
            reply_context={"sender_id": BOT}))
    assert env.identity == before


@pytest.mark.parametrize("route", ["native", "passive", "direct"])
@pytest.mark.parametrize("msg_id", [0, True, str(ROOT + 1), ROOT])
def test_reacquire_event_ids_are_not_coerced_before_validation(env, route, msg_id):
    assert asyncio.run(start(env))
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, route=route, msg_id=msg_id))
    assert env.identity == before


@pytest.mark.parametrize("route", ["native", "passive", "direct"])
def test_ambiguous_reacquire_receipts_cannot_bind_a_reply(env, route):
    env.send.return_value = None
    assert not asyncio.run(start(env))
    receipt(env)
    other = copy.deepcopy(env.identity["pending_tasks"][(CHAT, ROOT)])
    other["message_id"] = ROOT + 10
    env.identity["pending_tasks"][(CHAT, ROOT + 10)] = other
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env, route=route))
    assert env.identity == before


@pytest.mark.parametrize("field,bad", [
    ("account_id", ACCOUNT + 1), ("op_id", "f" * 32), ("source_module", "foreign"),
    ("cmd", concubine.CMD_CONCUBINE_ROMANCE), ("sent_at", True), ("send_started_at", NOW - 10),
    ("chat_id", CHAT - 1), ("message_id", ROOT + 1000),
])
def test_conflicting_reacquire_receipt_cannot_authorize_a_result(env, field, bad):
    env.send.return_value = None
    assert not asyncio.run(start(env))
    receipt(env)
    env.identity["pending_tasks"][(CHAT, ROOT)][field] = bad
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(reply(env))
    assert env.identity == before


def test_passive_acquisition_without_identity_hint_needs_one_owned_receipt(env):
    assert asyncio.run(start(env))
    state_module.set_identity_account(ID + 1, ACCOUNT + 1)
    other = state_module.get_identity_state(ID + 1)
    other.update(copy.deepcopy(env.identity))
    other[FIELD].update(identity_id=ID + 1, account_id=ACCOUNT + 1, op_id="e" * 32)
    context = {"family": SOURCE, "chat_id": CHAT, "root_msg_id": ROOT, "reply_to_msg_id": ROOT, "sender_id": BOT}
    before = copy.deepcopy(state_module._meta_state)
    assert not asyncio.run(reply(env, route="passive", context=context))
    assert state_module._meta_state == before
    state_module.remove_identity(ID + 1)
    assert asyncio.run(reply(env, route="passive", context=context))
    assert env.identity[FIELD]["status"] == "complete"


@pytest.mark.parametrize("text,outcome,delay", [
    ("\u8d21\u732e\u4e0d\u8db3", "resource_shortage", concubine.CONCUBINE_REACQUIRE_RETRY_SEC),
    ("\u5c1a\u672a\u7b51\u57fa", "not_eligible", concubine.CONCUBINE_REACQUIRE_RETRY_SEC),
    ("\u672a\u80fd\u5bfb\u5f97\u6709\u7f18\u4e4b\u4eba", "not_found", concubine.CONCUBINE_REACQUIRE_RETRY_SEC),
    (COOLDOWN, "cooldown", 7 * 3600 + 3 * 60 + 22 + concubine.CD_BUFFER_SEC),
])
def test_reacquire_refusal_deadline_uses_authoritative_reply_not_replay_clock(env, text, outcome, delay):
    if outcome == "not_found":
        state_module.update_send_as_profile(ID, sect_name="other")
    assert asyncio.run(start(env))
    receipt(env)
    env.clock[0] += 3600
    assert asyncio.run(reply(env, text))
    value = env.identity[FIELD]
    assert value["status"] == "complete" and value["result"]["outcome"] == outcome
    assert value["result"]["retry_at"] == NOW + 1 + delay
    assert env.identity["concubine_reacquire_blocked_until"] == NOW + 1 + delay
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()


def test_reacquire_command_redirection_is_limited_before_backoff(env):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, "\u4f60\u5e76\u975e\u661f\u5bab\u5f1f\u5b50"))
    first = copy.deepcopy(env.identity[FIELD])
    assert first["result"]["next_command"] == concubine.CMD_CONCUBINE_ROMANCE
    assert not asyncio.run(start(env))
    env.clock[0] = first["result"]["retry_at"]
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT,
                                            sent_at=env.clock[0], send_started_at=env.clock[0])
    assert asyncio.run(start(env))
    assert env.identity[FIELD]["command"] == concubine.CMD_CONCUBINE_ROMANCE
    assert env.identity[FIELD]["redirects"] == 1
    assert asyncio.run(reply(env, "\u4f60\u4e43\u661f\u5bab\u5f1f\u5b50", root=ROOT + 10,
                             msg_id=ROOT + 11, at=env.clock[0] + 1))
    second = env.identity[FIELD]
    assert second["result"]["next_command"] == ""
    assert second["result"]["retry_at"] == env.clock[0] + 1 + concubine.CONCUBINE_REACQUIRE_RETRY_SEC
    env.clock[0] += 60
    assert not asyncio.run(start(env))
    assert env.send.await_count == 2


@pytest.mark.parametrize("mismatch", ["none", "root", "chat"])
def test_acquisition_closes_only_its_exact_guard_and_pending_row(env, monkeypatch, mismatch):
    from model import action_guard

    monkeypatch.setattr(action_guard, "_recent_closed_command_guards", {})
    assert asyncio.run(start(env))
    receipt(env)
    guard = {"last_msg_id": ROOT + (mismatch == "root"), "last_chat_id": CHAT - (mismatch == "chat"),
             "command": env.identity[FIELD]["command"], "action_key": SOURCE,
             "first_sent_at": NOW, "last_sent_at": NOW, "attempt": 1}
    env.identity["action_guard_sessions"][SOURCE] = guard
    sibling = {"cmd": env.identity[FIELD]["command"], "family": SOURCE,
               "source_module": SOURCE, "op_id": "replacement"}
    env.identity["pending_tasks"][(CHAT, ROOT + 100)] = sibling
    assert asyncio.run(reply(env))
    assert (CHAT, ROOT) not in env.identity["pending_tasks"]
    assert env.identity["pending_tasks"][(CHAT, ROOT + 100)] == sibling
    assert (SOURCE in env.identity["action_guard_sessions"]) is (mismatch != "none")


@pytest.mark.parametrize("mode", ["false", "exception"])
def test_acquisition_guard_save_failure_keeps_result_and_retries_only_cleanup(env, monkeypatch, mode):
    from model import action_guard

    monkeypatch.setattr(action_guard, "_recent_closed_command_guards", {})
    assert asyncio.run(start(env))
    guard = {"last_msg_id": ROOT, "last_chat_id": CHAT, "command": env.identity[FIELD]["command"],
             "action_key": SOURCE, "first_sent_at": NOW, "last_sent_at": NOW, "attempt": 1}
    env.identity["action_guard_sessions"][SOURCE] = guard
    env.save.side_effect = [True, False if mode == "false" else OSError("guard save fixture")]
    assert asyncio.run(reply(env))
    completed = copy.deepcopy(env.identity[FIELD])
    assert completed["status"] == "complete" and env.identity["concubine_name"] == NAME
    assert env.identity["action_guard_sessions"][SOURCE] == guard
    env.save.side_effect = None
    with state_module.use_identity(ID):
        assert not asyncio.run(concubine.reacquire_actions.recover(NOW + 3))
    assert SOURCE not in env.identity["action_guard_sessions"]
    assert env.identity[FIELD] == completed
    env.send.assert_awaited_once()


@pytest.mark.parametrize("cancel", [False, True])
def test_acquisition_notification_failure_cannot_undo_completion(env, cancel):
    assert asyncio.run(start(env))
    env.audit.side_effect = asyncio.CancelledError() if cancel else OSError("notification fixture")
    if cancel:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(reply(env))
    else:
        assert asyncio.run(reply(env))
    before = copy.deepcopy(env.identity)
    assert before[FIELD]["status"] == "complete" and before["concubine_name"] == NAME
    assert not asyncio.run(reply(env))
    assert env.identity == before
    assert not asyncio.run(start(env))
    env.send.assert_awaited_once()


@pytest.mark.parametrize("unsent", [False, True])
def test_reacquire_redirection_budget_survives_status_refresh_and_unsent_retry(env, monkeypatch, unsent):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, "\u4f60\u5e76\u975e\u661f\u5bab\u5f1f\u5b50"))
    if unsent:
        env.clock[0] = env.identity[FIELD]["result"]["retry_at"]
        block = {"status": "none"}
        monkeypatch.setattr(concubine, "classify_game_send_block", lambda *_args: dict(block))

        async def blocked(*_args, **_kwargs):
            block.update(status="unsent", code="pre_send_guard", at=env.clock[0])
            return None

        env.send.side_effect = blocked
        assert not asyncio.run(start(env))
        assert env.identity[FIELD]["status"] == "unsent" and env.identity[FIELD]["redirects"] == 1
        env.send.side_effect = None
    env.clock[0] = NOW + concubine.CONCUBINE_PANEL_REUSE_MAX_AGE_SEC + 100
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT,
                                            sent_at=env.clock[0], send_started_at=env.clock[0])
    assert not asyncio.run(start(env))
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_STATUS
    with state_module.use_identity(ID):
        assert asyncio.run(concubine.handle_concubine_status_reply(
            ABSENT, env.clock[0] + 2, SimpleNamespace(id=ROOT + 10, chat_id=CHAT,
                                                    raw_text=concubine.CMD_CONCUBINE_STATUS),
            current_msg_id=ROOT + 11, current_chat_id=CHAT, observed_at=env.clock[0] + 1,
            reply_context={"sender_id": BOT}))
    env.clock[0] = env.identity["next_concubine_time"]
    env.send.return_value = SimpleNamespace(id=ROOT + 20, chat_id=CHAT,
                                            sent_at=env.clock[0], send_started_at=env.clock[0])
    assert asyncio.run(start(env))
    assert env.identity[FIELD]["command"] == concubine.CMD_CONCUBINE_ROMANCE
    assert env.identity[FIELD]["redirects"] == 1
    assert asyncio.run(reply(env, "\u4f60\u4e43\u661f\u5bab\u5f1f\u5b50", root=ROOT + 20,
                             msg_id=ROOT + 21, at=env.clock[0] + 1))
    assert env.identity[FIELD]["result"]["next_command"] == ""
    assert not asyncio.run(start(env))


@pytest.mark.parametrize("change", ["boolean", "timer", "attempts", "partner", "snapshot", "sibling"])
def test_early_acquisition_cannot_release_a_replaced_zero_anchor_wait(env, change):
    after = {}

    async def sent(*_args, **_kwargs):
        message = receipt(env)
        key, value = {
            "boolean": ("concubine_reacquire_msg_id", False),
            "timer": ("next_concubine_time", NOW + 99999),
            "attempts": ("concubine_reacquire_attempts", 100),
            "partner": ("concubine_name", "replacement"),
            "snapshot": ("concubine_last_snapshot_at", NOW + 1),
            "sibling": ("concubine_heart_session", {"invalid": True}),
        }[change]
        env.identity[key] = value
        env.identity["concubine_auto_reacquire"] = False
        after.update(copy.deepcopy(env.identity))
        assert await reply(env)
        return message

    env.send.side_effect = sent
    assert asyncio.run(start(env))
    assert env.identity[FIELD]["status"] == "complete"
    for key in ("concubine_phase", "concubine_reacquire_msg_id", "concubine_reacquire_attempts", "next_concubine_time"):
        assert env.identity[key] == after[key] and type(env.identity[key]) is type(after[key])


@pytest.mark.parametrize("text,outcome", [
    ("\u4f60\u5df2\u6709\u9053\u4fa3", "exists"),
    ("\u4f8d\u59be\u6b63\u5728\u8fdc\u822a\u9014\u4e2d\u3002", "voyage_lock"),
])
def test_existing_partner_refusal_requires_calibration_before_another_acquisition(env, text, outcome):
    assert asyncio.run(start(env))
    assert asyncio.run(reply(env, text))
    assert env.identity[FIELD]["result"]["outcome"] == outcome
    assert env.identity["concubine_availability"] == "unknown"
    assert env.identity["concubine_last_snapshot_at"] == 0
    env.clock[0] = env.identity["next_concubine_time"]
    env.send.return_value = SimpleNamespace(id=ROOT + 10, chat_id=CHAT,
                                            sent_at=env.clock[0], send_started_at=env.clock[0])
    assert not asyncio.run(start(env))
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_STATUS
    assert env.identity[FIELD]["status"] == "complete"
