import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import explore_rift, tianxing


NOW = 1780000000.0
IDENTITY = 990560001
ACCOUNT = 7551
CHAT = -100560001
OTHER_CHAT = -100560002
BOT = 880560001
ROOT = 5601
RESULT = 5602
PANEL = "\u3010\u5929\u673a\u76d8\u3011\n\u5f53\u524d\u63a8\u547d: \u65e0\n\u5f53\u524d\u6539\u547d: \u63a2\u7d22\uff08\u5269\u4f59 3600\u79d2\uff09"
START = "\u4f60\u8fd0\u8f6c\u5168\u8eab\u6cd5\u529b\uff0c\u6495\u5f00\u4e00\u9053\u6f06\u9ed1\u7684\u7a7a\u95f4\u88c2\u7f1d"
FINAL = "\u3010\u906d\u9047\u98ce\u66b4\u3011\n\u4fee\u4e3a\u5012\u9000\u4e86 300 \u70b9\uff01"


def row(text, *, msg_id=RESULT, root=ROOT, sender=BOT, chat=CHAT, at=NOW - 30, received=NOW - 1, kind="message", **extra):
    return {
        "ts": datetime.fromtimestamp(received, explore_rift.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
        "event_type": kind, "server_event_at": at,
        "chat_id": chat, "message_id": msg_id, "reply_to_msg_id": root,
        "sender_id": sender, "text": text, **extra,
    }


def command(mode="reply", **extra):
    text = explore_rift.CMD_TIANXING_PANEL if mode == "panel" else explore_rift.CMD_EXPLORE_RIFT
    return row(text, msg_id=ROOT, root=0, sender=IDENTITY, at=NOW - 120, received=NOW - 120, **extra)


@pytest.fixture
def env(monkeypatch, tmp_path):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    state_module.set_game_bot_ids([BOT])
    state_module.set_game_group_route_config({"enabled": True, "primary_group_id": CHAT, "backup_group_ids": [OTHER_CHAT]})
    identity = state_module.get_identity_state(IDENTITY)
    identity.update(explore_rift_enabled=True, tianxing_enabled=True, tianxing_observation={
        "explore_rift_unknown_snapshot": {"panel_msg_id": ROOT, "panel_sent_at": NOW - 120},
    })
    send = AsyncMock(return_value=None)
    for module in (explore_rift, tianxing):
        monkeypatch.setattr(module, "save_state", Mock())
        monkeypatch.setattr(module, "send_game_command", send)
    monkeypatch.setattr(explore_rift, "send_audit_log", AsyncMock())
    monkeypatch.setattr(explore_rift, "MESSAGES_DIR", str(tmp_path))

    def write(entries):
        day = datetime.fromtimestamp(NOW, explore_rift.TZ_LOCAL).date().isoformat()
        (tmp_path / f"{day}.log").write_text("\n".join(json.dumps(item) for item in entries) + "\n", encoding="utf-8")

    with state_module.use_identity(IDENTITY):
        yield SimpleNamespace(identity=identity, write=write, send=send)
    send.assert_not_awaited()
    state_module._meta_state.clear()
    state_module._meta_state.update(saved)


def read(mode):
    if mode == "panel":
        return explore_rift._recover_unknown_rift_panel_from_message_log(NOW)
    if mode == "result":
        return explore_rift._find_logged_explore_rift_result_message(RESULT, NOW)
    return explore_rift._find_logged_explore_rift_reply(ROOT, NOW)


@pytest.mark.parametrize("mode", ["panel", "reply", "result"])
@pytest.mark.parametrize("problem", [
    "other_chat", "other_sender", "missing_sender", "missing_chat", "other_root",
    "sent_reply", "missing_time", "boolean_time", "future_time", "nan_time",
    "other_identity_command", "no_command", "wrong_command", "bad_account",
])
def test_unowned_or_invalid_log_reply_cannot_update_state(env, mode, problem):
    outgoing = command(mode)
    reply = row(PANEL if mode == "panel" else FINAL)
    if problem == "other_chat":
        reply["chat_id"] = OTHER_CHAT
    elif problem == "other_sender":
        reply["sender_id"] = BOT + 1
    elif problem == "missing_sender":
        reply.pop("sender_id")
    elif problem == "missing_chat":
        reply.pop("chat_id")
    elif problem == "other_root":
        reply["reply_to_msg_id"] = ROOT + 100
    elif problem == "sent_reply":
        reply["event_type"] = "sent"
    elif problem == "missing_time":
        reply.pop("server_event_at")
    elif problem == "boolean_time":
        reply["server_event_at"] = True
    elif problem == "future_time":
        reply["server_event_at"] = NOW + 30
    elif problem == "nan_time":
        reply["server_event_at"] = float("nan")
    elif problem == "other_identity_command":
        outgoing["sender_id"] = IDENTITY + 1
    elif problem == "wrong_command":
        outgoing["text"] = ".unrelated"
    elif problem == "bad_account":
        outgoing["account_id"] = ACCOUNT + 1
    env.write(([outgoing] if problem != "no_command" else []) + [reply])
    before = copy.deepcopy(env.identity)
    assert not read(mode)
    assert env.identity == before


@pytest.mark.parametrize("mode", ["panel", "reply", "result"])
def test_reply_before_command_server_time_is_not_evidence(env, mode):
    env.write([command(mode), row(PANEL if mode == "panel" else FINAL, at=NOW - 200)])
    assert not read(mode)


@pytest.mark.parametrize("mode", ["panel", "reply", "result"])
def test_valid_replay_uses_server_time_and_owned_context(env, monkeypatch, mode):
    env.write([command(mode), row(PANEL if mode == "panel" else FINAL, kind="edit")])
    if mode == "panel":
        assert read(mode)
        observed = env.identity["tianxing_observation"]
        assert observed["current_change_until"] == NOW - 30 + 3600
        assert observed["effect_evidence"]["change"]["chat_id"] == CHAT
        assert observed["effect_evidence"]["change"]["msg_id"] == RESULT
        return
    handler = AsyncMock(return_value=True)
    monkeypatch.setattr(explore_rift, "handle_explore_rift_reply", handler)
    if mode == "result":
        env.identity["explore_rift_pending_result_msg_id"] = RESULT
        assert asyncio.run(explore_rift._recover_pending_explore_rift_result_from_message_log(NOW)) == "result"
    else:
        assert asyncio.run(explore_rift._recover_explore_rift_from_message_log(NOW, command_msg_id=ROOT)) == "result"
    args, kwargs = handler.await_args
    assert args == (FINAL, NOW - 30)
    assert kwargs["reply_to"].id == ROOT
    assert kwargs["reply_to"].chat_id == CHAT
    assert kwargs["reply_context"]["root_msg_id"] == ROOT
    assert kwargs["reply_context"]["send_as_id"] == IDENTITY
    assert kwargs["reply_context"]["server_event_at"] == NOW - 30
    assert kwargs["reply_context"]["processed_at"] == NOW


@pytest.mark.parametrize("mode", ["panel", "reply", "result"])
def test_late_original_cannot_replace_newer_server_edit(env, mode):
    newest = PANEL if mode == "panel" else FINAL
    old = PANEL.replace("3600", "7200") if mode == "panel" else START
    env.write([command(mode), row(newest, kind="edit", received=NOW - 2), row(old, at=NOW - 60)])
    result = read(mode)
    assert result
    if mode == "panel":
        assert env.identity["tianxing_observation"]["current_change_until"] == NOW - 30 + 3600
    else:
        assert result["text"] == FINAL


@pytest.mark.parametrize("mode", ["panel", "reply", "result"])
def test_conflicting_same_second_edits_are_not_guessed(env, mode):
    text = PANEL if mode == "panel" else FINAL
    changed = text.replace("3600", "7200") if mode == "panel" else FINAL.replace("300", "400")
    env.write([command(mode), row(text, kind="edit", received=NOW - 2), row(changed, kind="edit")])
    assert not read(mode)


@pytest.mark.parametrize("mode", ["panel", "reply", "result"])
def test_same_ids_in_two_owned_chats_are_ambiguous(env, mode):
    text = PANEL if mode == "panel" else FINAL
    env.write([command(mode), command(mode, chat=OTHER_CHAT), row(text), row(text, chat=OTHER_CHAT)])
    assert not read(mode)


def test_command_discovery_does_not_dedupe_before_verifying_sender(env):
    own = command()
    foreign = {**own, "sender_id": IDENTITY + 1}
    env.write([foreign, own])
    result = explore_rift._find_recent_logged_explore_rift_command(NOW)
    assert result and result["msg_id"] == ROOT
    assert result["chat_id"] == CHAT


def test_unknown_command_discovery_requires_one_unambiguous_command(env):
    env.write([command(), {**command(), "message_id": ROOT + 10}])
    assert not explore_rift._find_recent_logged_explore_rift_command(NOW)


def test_late_old_command_is_not_the_current_unknown_send(env):
    old = {**command(), "server_event_at": NOW - 10000}
    env.write([old])
    assert not explore_rift._find_recent_logged_explore_rift_command(NOW)


@pytest.mark.parametrize("field,value", [
    ("panel_msg_id", "bad"), ("panel_msg_id", True), ("panel_msg_id", float("inf")),
    ("panel_sent_at", "bad"), ("panel_sent_at", float("nan")), ("panel_sent_at", NOW + 30),
])
def test_corrupt_panel_snapshot_does_not_recover(env, field, value):
    env.identity["tianxing_observation"]["explore_rift_unknown_snapshot"][field] = value
    env.write([command("panel"), row(PANEL)])
    assert not read("panel")


@pytest.mark.parametrize("mode", ["panel", "reply", "result"])
def test_latest_unrecognized_edit_does_not_resurrect_an_old_result(env, mode):
    text = PANEL if mode == "panel" else FINAL
    env.write([command(mode), row(text, at=NOW - 60), row("unrecognized", kind="edit")])
    assert not read(mode)


@pytest.mark.parametrize("reload", [False, True])
def test_registered_command_can_recover_reply_that_preceded_receipt(env, monkeypatch, tmp_path, reload):
    env.identity["pending_tasks"][(CHAT, ROOT)] = {
        "cmd": explore_rift.CMD_EXPLORE_RIFT, "chat_id": CHAT,
        "sent_at": NOW - 1, "send_started_at": NOW - 120,
    }
    env.write([row(FINAL)])
    if reload:
        from model import persistence

        monkeypatch.setattr(persistence, "DB_FILE", str(tmp_path / "rift-recovery.db"))
        assert persistence.save_state()
        assert persistence.load_state()
    result = read("reply")
    assert result and result["ts"] == NOW - 30
    assert result["root_msg_id"] == ROOT and result["chat_id"] == CHAT


def test_pending_command_in_another_chat_cannot_bind_a_reply(env):
    env.identity["pending_tasks"][(OTHER_CHAT, ROOT)] = {
        "cmd": explore_rift.CMD_EXPLORE_RIFT, "chat_id": OTHER_CHAT,
        "sent_at": NOW - 100, "send_started_at": NOW - 120,
    }
    env.write([row(FINAL)])
    assert not read("reply")


def test_log_recovery_limits_each_daily_read(env, monkeypatch):
    reads = []

    def bounded_read(path, *, max_bytes):
        reads.append((path, max_bytes))
        return []

    monkeypatch.setattr(explore_rift, "_read_log_tail_lines", bounded_read)
    assert not read("reply")
    assert 1 <= len(reads) <= 3
    assert all(limit == 512 * 1024 for _path, limit in reads)


@pytest.mark.parametrize("invalid", [None, [], "bad", 42, {"message_id": []}, {"event_type": []}, {"event_type": "message", "text": None}])
def test_malformed_log_rows_do_not_crash_recovery(env, invalid):
    env.write([invalid])
    assert not explore_rift._find_recent_logged_explore_rift_command(NOW)
    assert not read("panel")
    assert not read("reply")
    assert not read("result")
