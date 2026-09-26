import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app, app_runtime, runtime
from model import state as state_module
from model.features import hehuan, passive_inbox


IDENTITY = 990690001
ACCOUNT = 7691
CHAT = -100690001
OTHER_CHAT = -100690002
BOT = 880690001
ROOT = 6901
REPLY = 6902
NOW = 1790406937.0
PENDING = "契印感应，双方灵力开始共鸣，准备进行温养双修..."
FINAL = (
    "【温养双修·大成】\n"
    "在同参契印的加持下，你与 @WalterWA2000 灵力完美交融，事半功倍！\n"
    "@wisemole 修为增加了 61 点，并获得 15 点宗门贡献！\n"
    "@WalterWA2000 修为增加了 72 点！"
)


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(IDENTITY, ACCOUNT)
    state_module.set_game_bot_ids([BOT])
    state_module.update_send_as_profile(IDENTITY, username="wisemole", sect_name="合欢宗")
    identity = state_module.get_identity_state(IDENTITY)
    identity["hehuan_enabled"] = True
    identity["hehuan_observation"] = hehuan.normalize_hehuan_observation({})
    identity["pending_tasks"][(CHAT, ROOT)] = {
        "cmd": ".双修 温养", "chat_id": CHAT, "sent_at": NOW, "timeout": 160,
        "retry": 0, "max_retry": 0,
    }
    identity["pending_tasks"][(OTHER_CHAT, ROOT)] = dict(identity["pending_tasks"][(CHAT, ROOT)], chat_id=OTHER_CHAT)
    monkeypatch.setattr(app_runtime, "_runtime_event_claims", {})
    monkeypatch.setattr(app_runtime, "_runtime_message_consumed", {})
    monkeypatch.setattr(runtime, "_reply_chain_tracker", {})
    monkeypatch.setattr(app, "_remember_early_routed_reply", Mock())
    monkeypatch.setattr(app, "schedule_cleanup", AsyncMock())
    monkeypatch.setattr(hehuan, "save_state", Mock())
    monkeypatch.setattr(passive_inbox, "save_state", Mock())
    try:
        yield identity
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(saved)


def deliver(text, *, at, event_type="message", path="route", root=ROOT, family="hehuan_dual", pending=None):
    event = SimpleNamespace(id=REPLY, chat_id=CHAT, sender_id=BOT, raw_text=text, server_event_at=at)
    reply_to = SimpleNamespace(id=root, chat_id=CHAT, raw_text=".双修 温养")
    context = {
        "send_as_id": IDENTITY, "chat_id": CHAT, "family": family,
        "root_msg_id": root, "reply_to_msg_id": root, "reply_to_command": ".双修 温养",
    }
    if path == "route":
        return asyncio.run(app._handle_routed_reply_event(
            event, text, at, reply_to, context, event_kind=event_type,
        ))
    entry = {
        "chat_id": CHAT, "message_id": REPLY, "sender_id": BOT,
        "reply_to_msg_id": root, "text": text, "ts_epoch": at,
        "server_event_at": at, "event_type": event_type,
    }
    return asyncio.run(app._replay_pending_log_replies(IDENTITY, root, pending, [entry], at + 2))


@pytest.mark.parametrize("path", ["route", "replay"])
def test_real_warm_reply_keeps_pending_until_final_edit(env, path):
    pending = env["pending_tasks"][(CHAT, ROOT)]
    assert deliver(PENDING, at=NOW + 2, path=path, pending=pending)
    assert (CHAT, ROOT) in env["pending_tasks"]
    assert env["hehuan_observation"]["last_result"] == "pending"
    assert deliver(FINAL, at=NOW + 9, event_type="edit", path=path, pending=pending)
    assert (CHAT, ROOT) not in env["pending_tasks"]
    assert (OTHER_CHAT, ROOT) in env["pending_tasks"]
    assert env["hehuan_observation"]["last_result"] == "success"
    assert env["hehuan_observation"]["last_warm_success_at"] == NOW + 9


def test_old_final_reply_clears_its_root_without_rewinding_newer_business_state(env):
    pending = env["pending_tasks"][(CHAT, ROOT)]
    env["hehuan_observation"] = hehuan.normalize_hehuan_observation({
        "last_result": "success", "last_observed_at": NOW + 3600,
        "last_warm_success_at": NOW + 3600, "next_hehuan_time": NOW + 7200,
    })
    assert deliver(FINAL, at=NOW + 9, event_type="edit", path="replay", pending=pending)
    assert (CHAT, ROOT) not in env["pending_tasks"]
    assert env["hehuan_observation"]["last_warm_success_at"] == NOW + 3600
    assert env["hehuan_observation"]["next_hehuan_time"] == NOW + 7200


def test_unrelated_family_and_unparsed_text_cannot_clear_warm_pending(env):
    assert not deliver(FINAL, at=NOW + 9, family="hehuan_contract")
    assert not deliver("收到，稍后再试", at=NOW + 10)
    assert (CHAT, ROOT) in env["pending_tasks"]
