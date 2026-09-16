import asyncio
import copy
from dataclasses import replace

import pytest

from model import persistence, state as state_module
from model.features import wanxin
from test_wanxin_lifecycle import CHAT, NOW, OWNER, deliver
from test_wanxin_lifecycle import env as env
from test_wanxin_liveness import persistent_env as persistent_env
from test_wanxin_reply_contract import COST, GAIN, HEADERS, SAMPLES
from yinluo_native_support import native_reply


def current_snapshot(env, *, affinity=200, at=NOW + 10):
    env.owner.update(
        concubine_name="\u5357\u5bab\u5a49\u00b7\u6708\u5f71",
        concubine_kind="\u9053\u5fc3\u4f8d\u59be", concubine_location="\u968f\u884c\u4e2d",
        concubine_affinity=affinity, concubine_last_snapshot_at=at,
    )


def moon_panel(*, at=NOW + 1):
    return native_reply(
        OWNER, wanxin.CMD_WANXIN_MOON_STATUS, SAMPLES["wanxin_moon_panel"],
        at, root=100, command_at=NOW,
    )


def test_older_moon_panel_cannot_replace_a_newer_concubine_snapshot(env):
    current_snapshot(env)
    assert asyncio.run(deliver(moon_panel(), NOW + 20))
    assert env.owner["concubine_affinity"] == 200
    assert env.owner["concubine_last_snapshot_at"] == NOW + 10


def test_edited_old_moon_panel_cannot_borrow_its_edit_time_as_a_new_snapshot(env):
    current_snapshot(env, at=NOW - 1)
    original = moon_panel()
    assert asyncio.run(deliver(original))
    current_snapshot(env)
    edited = replace(original, event_type="edit", server_event_at=NOW + 20,
                     text=original.text.replace("\u60c5\u7f18\uff1a314", "\u60c5\u7f18\uff1a315"))
    assert asyncio.run(deliver(edited, NOW + 21))
    assert env.owner["concubine_affinity"] == 200
    assert env.owner["concubine_last_snapshot_at"] == NOW + 10


@pytest.mark.parametrize("action,body,affinity", [("moon_greet", GAIN, 209), ("moon_seal", COST, 176)])
def test_late_affinity_effect_already_covered_by_new_snapshot_is_not_counted_twice(env, action, body, affinity):
    current_snapshot(env, affinity=affinity)
    received = native_reply(
        OWNER, wanxin.WANXIN_ACTION_COMMANDS[action], HEADERS[action] + "\n" + body,
        NOW + 1, root=100, command_at=NOW,
    )
    assert asyncio.run(deliver(received, NOW + 20))
    assert env.owner["concubine_affinity"] == affinity
    assert env.owner["wanxin_observation"][f"next_{action}_time"] > NOW


@pytest.mark.parametrize("action,body,affinity", [
    ("moon_status", SAMPLES["wanxin_moon_panel"], 314),
    ("moon_greet", HEADERS["moon_greet"] + "\n" + GAIN, 209),
    ("moon_seal", HEADERS["moon_seal"] + "\n" + COST, 176),
])
def test_supported_new_affinity_results_are_not_dropped(env, action, body, affinity):
    current_snapshot(env, at=NOW - 10)
    received = native_reply(OWNER, wanxin.WANXIN_ACTION_COMMANDS[action], body, NOW + 1, root=100, command_at=NOW)
    assert asyncio.run(deliver(received))
    assert env.owner["concubine_affinity"] == affinity


def test_old_reply_closes_only_its_pending_root_and_survives_sqlite_reload(persistent_env):
    env = persistent_env
    current_snapshot(env, affinity=209)
    received = native_reply(OWNER, wanxin.CMD_WANXIN_MOON_GREET, HEADERS["moon_greet"] + "\n" + GAIN,
                            NOW + 1, root=100, command_at=NOW)
    env.owner["pending_tasks"][(CHAT, 100)] = {
        "cmd": wanxin.CMD_WANXIN_MOON_GREET, "family": "wanxin_moon_greet",
        "chat_id": CHAT, "account_id": OWNER, "sent_at": NOW,
    }
    sibling = {"cmd": wanxin.CMD_WANXIN_MOON_SEAL, "chat_id": CHAT, "account_id": OWNER, "sent_at": NOW + 10}
    env.owner["pending_tasks"][(CHAT, 200)] = sibling
    assert asyncio.run(deliver(received, NOW + 20))
    assert env.owner["pending_tasks"] == {(CHAT, 200): sibling}
    assert env.owner["concubine_affinity"] == 209
    before = copy.deepcopy(env.owner["wanxin_observation"])
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.owner = state_module.get_identity_state(OWNER)
    reloaded_pending = copy.deepcopy(env.owner["pending_tasks"])
    assert set(reloaded_pending) == {(CHAT, 200)}
    assert reloaded_pending[(CHAT, 200)]["cmd"] == sibling["cmd"]
    assert reloaded_pending[(CHAT, 200)]["sent_at"] == sibling["sent_at"]
    assert asyncio.run(deliver(received, NOW + 21))
    assert env.owner["wanxin_observation"] == before
    assert env.owner["concubine_affinity"] == 209
    assert env.owner["pending_tasks"] == reloaded_pending


def test_failed_old_result_save_keeps_current_affinity_and_original_pending(persistent_env):
    env = persistent_env
    current_snapshot(env, affinity=176)
    received = native_reply(OWNER, wanxin.CMD_WANXIN_MOON_SEAL, HEADERS["moon_seal"] + "\n" + COST,
                            NOW + 1, root=100, command_at=NOW)
    env.owner["pending_tasks"][(CHAT, 100)] = {
        "cmd": wanxin.CMD_WANXIN_MOON_SEAL, "family": "wanxin_moon_seal",
        "chat_id": CHAT, "account_id": OWNER, "sent_at": NOW,
    }
    assert persistence.save_state()
    before = copy.deepcopy(env.owner)
    conn = persistence.get_db_conn()
    conn.execute(f"CREATE TEMP TRIGGER fail_affinity_reply BEFORE INSERT ON identity_runtime_state "
                 f"WHEN NEW.send_as_id = {OWNER} BEGIN SELECT RAISE(ABORT, 'affinity failure'); END")
    try:
        assert not asyncio.run(deliver(received, NOW + 20))
        assert env.owner == before
    finally:
        conn.execute("DROP TRIGGER fail_affinity_reply")
    assert asyncio.run(deliver(received, NOW + 20))
    assert env.owner["concubine_affinity"] == 176
    assert not env.owner["pending_tasks"]
