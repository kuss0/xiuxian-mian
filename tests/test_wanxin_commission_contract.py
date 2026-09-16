import asyncio
import copy
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from model import persistence, state as state_module
from model.features import wanxin
from test_wanxin_lifecycle import CHAT, HELPER, NOW, OWNER, deliver, receipt, schedule
from test_wanxin_lifecycle import env as env
from test_wanxin_liveness import persistent_env as persistent_env
import test_wanxin_commission_replay as replay_fixture
from yinluo_native_support import native_reply


PUBLISH = "\u3010\u89e3\u5492\u59d4\u6258\u5df2\u53d1\u5e03\u3011"
ACCEPT = "\u3010\u5492\u5951\u534f\u5b9a\u5df2\u6210\u3011"
IDENTIFY = "\u3010\u9634\u7f57\u8fa8\u5492\u3011"
ID_LINE = "\u59d4\u6258 ID\uff1a5"
PUBLISHED = PUBLISH + "\n" + ID_LINE
ACCEPT_BODY = "\u9634\u7f57\u5b97\u5f1f\u5b50 @helper \u5df2\u63a5\u53d6 @owner \u7684\u89e3\u5492\u59d4\u6258\u3002"
IDENTIFY_BODY = "@helper \u66ff @owner \u9501\u5b9a\u5492\u6e90\u3002"
SOURCE_GAIN = "\u5492\u6e90 +20"
CONTRIB_GAIN = "\u5492\u5e08\u8d21\u732e +120"
IDENTIFIED = IDENTIFY + "\n" + IDENTIFY_BODY + "\n" + SOURCE_GAIN + "\uff0c" + CONTRIB_GAIN + "\u3002"
CANCELLED = "\u89e3\u5492\u59d4\u6258\u5df2\u53d6\u6d88\uff0c\u5df2\u9000\u56de 1 \u7075\u77f3\u3002"
NO_CANCEL = "\u4f60\u5f53\u524d\u6ca1\u6709\u53ef\u53d6\u6d88\u7684\u89e3\u5492\u59d4\u6258\u3002"
CANCEL_BLOCKED = "\u59d4\u6258\u5df2\u88ab\u63a5\u53d6\uff0c\u65e0\u6cd5\u76f4\u63a5\u53d6\u6d88\u3002"
EXISTING = "\u4f60\u5df2\u6709\u8fdb\u884c\u4e2d\u7684\u89e3\u5492\u59d4\u6258\uff08ID: 5\uff09\uff0c\u4e0d\u53ef\u91cd\u590d\u53d1\u5e03\u3002"
WAIT = "\u8fa8\u8ba4\u5492\u7eb9 \u51b7\u5374 4 \u5c0f\u65f6\uff0c\u8bf7\u5728 3\u5c0f\u65f6 \u540e\u518d\u8bd5\u3002"
PROCESSING = "\u6b63\u5728\u5904\u7406\uff0c\u8bf7\u7a0d\u5019\u3002"
COMMANDS = {
    action: wanxin.WANXIN_ACTION_COMMANDS[action] + suffix
    for action, suffix in (("publish", " 1"), ("accept", " 5"), ("cancel", ""), ("identify", " @owner"))
}


def parse(action, text):
    return wanxin.parse_wanxin_text(text, now=NOW, family=wanxin.WANXIN_ACTION_FAMILIES[action])


@pytest.mark.parametrize("action,header", [("publish", PUBLISH), ("accept", ACCEPT), ("identify", IDENTIFY)])
@pytest.mark.parametrize("suffix", ["", "\n" + PROCESSING])
def test_commission_title_or_acknowledgement_without_facts_is_not_terminal(action, header, suffix):
    assert parse(action, header + suffix)["type"] == "unknown"


@pytest.mark.parametrize("value", ["", "0", "-5", "+5", "5.5", "5foo", "\uff15", str(2 ** 63)])
def test_publication_id_is_explicit_complete_positive_and_bounded(value):
    assert parse("publish", PUBLISH + "\n" + ID_LINE.replace("5", value))["type"] == "unknown"
    assert parse("publish", EXISTING.replace("5", value))["type"] == "unknown"


@pytest.mark.parametrize("text", [
    PUBLISHED + "\n" + ID_LINE, PUBLISHED + "\n" + ID_LINE.replace("5", "6"),
    EXISTING + "\n" + EXISTING, PUBLISHED + "\n" + EXISTING,
    PUBLISHED + "\n" + CANCELLED, PUBLISHED + "\n" + PROCESSING,
    "\u793a\u4f8b\uff1a" + PUBLISHED,
])
def test_ambiguous_or_instructional_publication_does_not_replace_commission(text):
    assert parse("publish", text)["type"] == "unknown"


@pytest.mark.parametrize("body", [
    ACCEPT_BODY + "\n" + ACCEPT_BODY,
    ACCEPT_BODY + "\n" + ACCEPT_BODY.replace("@owner", "@other"),
    ACCEPT_BODY + "\n" + CANCELLED,
    ACCEPT_BODY + "\n" + PROCESSING,
    "\u793a\u4f8b\uff1a" + ACCEPT_BODY,
    ACCEPT_BODY.replace("@owner ", "@" + "o" * 33 + " "),
    ACCEPT_BODY.replace("@helper ", "@helper\u574f "),
])
def test_acceptance_requires_one_complete_helper_and_owner_pair(body):
    assert parse("accept", ACCEPT + "\n" + body)["type"] == "unknown"


@pytest.mark.parametrize("text", [
    IDENTIFY + "\n" + IDENTIFY_BODY,
    IDENTIFY + "\n" + IDENTIFY_BODY + "\n" + SOURCE_GAIN,
    IDENTIFY + "\n" + IDENTIFY_BODY + "\n" + CONTRIB_GAIN,
    IDENTIFIED.replace("+20", "+20.5"),
    IDENTIFIED.replace("+120", "+120.5"),
    IDENTIFIED.replace("+20", "+" + str(2 ** 63)),
    IDENTIFIED.replace("+120", "+" + str(2 ** 63)),
    IDENTIFIED + "\n" + SOURCE_GAIN,
    IDENTIFIED + "\n" + CONTRIB_GAIN,
    IDENTIFIED + "\n" + IDENTIFY_BODY.replace("@owner", "@other"),
    IDENTIFIED + "\n" + PROCESSING,
    IDENTIFIED + "\n" + WAIT,
    IDENTIFIED.replace(IDENTIFY_BODY, "\u672a\u80fd\u9501\u5b9a\u5492\u6e90\u3002"),
    IDENTIFIED.replace(IDENTIFY_BODY, "\u793a\u4f8b\uff1a" + IDENTIFY_BODY),
])
def test_identification_needs_unique_targeted_positive_facts_not_zero_defaults(text):
    assert parse("identify", text)["type"] == "unknown"


@pytest.mark.parametrize("text", [
    "\u5c1a\u672a\u786e\u8ba4" + CANCELLED,
    "\u793a\u4f8b\uff1a" + CANCELLED,
    CANCELLED + "\n" + PROCESSING,
    CANCELLED + "\n" + CANCEL_BLOCKED,
    NO_CANCEL + "\n" + CANCEL_BLOCKED,
    "\u5c1a\u672a\u786e\u8ba4" + NO_CANCEL,
    "\u793a\u4f8b\uff1a" + CANCEL_BLOCKED,
])
def test_cancellation_phrases_do_not_override_uncertainty_or_conflicts(text):
    assert parse("cancel", text)["type"] == "unknown"


@pytest.mark.parametrize("text", [CANCELLED, NO_CANCEL, NO_CANCEL.removeprefix("\u4f60")])
def test_explicit_cancelled_and_absent_commission_remain_terminal(text):
    assert parse("cancel", text)["type"] == "commission_cancelled"


def test_identify_header_cannot_hide_actual_cooldown():
    parsed = parse("identify", IDENTIFY + "\n" + WAIT)
    assert (parsed["type"], parsed["cooldown_action"]) == ("cooldown", "identify")
    assert parsed["next_time"] == NOW + 10800 + wanxin.CD_BUFFER_SEC


@pytest.mark.parametrize("action,text", [
    ("publish", PUBLISHED + "\n\u7075\u77f3\u4e0d\u8db3\uff0c\u65e0\u6cd5\u53d1\u5e03\u3002"),
    ("publish", PUBLISHED + "\n\u672a\u6267\u884c\u53d1\u5e03\u3002"),
    ("accept", ACCEPT + "\n" + ACCEPT_BODY + "\n\u672a\u6210\u529f\u63a5\u53d6\u3002"),
    ("accept", ACCEPT + "\n" + ACCEPT_BODY + "\n\u65e0\u6cd5\u63a5\u53d6\u8be5\u59d4\u6258\u3002"),
    ("identify", IDENTIFIED + "\n\u672a\u6267\u884c\u8fa8\u8ba4\u5492\u7eb9\u3002"),
])
def test_positive_contract_does_not_override_explicit_non_execution(action, text):
    assert parse(action, text)["type"] == "unknown"


def test_publication_waiting_for_helper_is_not_a_send_in_progress():
    text = PUBLISHED + "\n\u6b63\u5728\u7b49\u5f85\u5492\u5e08\u63a5\u53d6\u3002"
    assert parse("publish", text)["type"] == "commission_published"


def test_identification_zero_and_contribution_alias_are_explicit_facts():
    for text in (IDENTIFIED.replace("+20", "+0").replace("+120", "+0"),
                 IDENTIFIED.replace(CONTRIB_GAIN, "\u8d21\u732e +120")):
        assert parse("identify", text)["type"] == "assist_identify_success"
    mixed = IDENTIFIED + "\n\u8d21\u732e +120"
    assert parse("identify", mixed)["type"] == "unknown"


@pytest.mark.parametrize("value", ["-1", "1.5", "\uff11", str(2 ** 63)])
def test_invalid_refund_does_not_close_cancellation(value):
    assert parse("cancel", CANCELLED.replace(" 1 ", " " + value + " "))["type"] == "unknown"


@pytest.mark.parametrize("which", ["publish", "accept"])
def test_latest_incomplete_edit_cannot_prove_an_external_commission(which):
    rows = replay_fixture.logs(completed=True)
    original = rows[1 if which == "publish" else 3]
    text = PUBLISH if which == "publish" else replay_fixture.ACCEPT + "\n" + replay_fixture.ACCEPT.splitlines()[1]
    rows.append({**original, "event_type": "edit", "server_event_at": replay_fixture.BASE + 30, "text": text})
    commission = {"id": 23, "publish_msg_id": 100}
    assert replay_fixture.find(rows, commission) is None
    assert replay_fixture.find(list(reversed(rows)), commission) is None


async def start_owned(env, action):
    observed = env.owner["wanxin_observation"]
    if action != "publish":
        observed["commission"].update(id=5, published_at=NOW - 100, publish_msg_id=50, owner_username="owner")
    if action == "identify":
        observed["commission"].update(accepted=True, accepted_at=NOW - 50, helper_username="helper")
    actor = HELPER if action in {"accept", "identify"} else OWNER
    sender = AsyncMock(return_value=receipt())
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    with state_module.use_identity(OWNER):
        assert await wanxin._send_nonfinancial_action(
            observed, action, COMMANDS[action], NOW, send_as_id=actor,
        )
    pending = copy.deepcopy(env.owner["wanxin_observation"]["pending"])
    actor_state = state_module.get_identity_state(actor)
    actor_state["pending_tasks"][(CHAT, 100)] = {
        "cmd": pending["command"], "family": pending["family"], "chat_id": CHAT,
        "account_id": actor, "sent_at": NOW, "op_id": pending["op_id"],
    }
    return native_reply(actor, COMMANDS[action], "", NOW + 1, root=100, command_at=NOW)


@pytest.mark.parametrize("action,partial,complete", [
    ("publish", PUBLISH, PUBLISHED),
    ("accept", ACCEPT + "\n" + ACCEPT_BODY + "\n" + ACCEPT_BODY, ACCEPT + "\n" + ACCEPT_BODY),
    ("identify", IDENTIFY, IDENTIFIED),
    ("cancel", CANCELLED + "\n" + PROCESSING, NO_CANCEL),
])
def test_owned_incomplete_result_retains_chain_until_its_complete_edit(env, action, partial, complete):
    event = asyncio.run(start_owned(env, action))
    before = copy.deepcopy(env.owner["wanxin_observation"])
    actor = state_module.get_identity_state(event.identity_id)
    pending = copy.deepcopy(actor["pending_tasks"])
    assert not asyncio.run(deliver(replace(event, text=partial)))
    observed = env.owner["wanxin_observation"]
    assert observed["pending"] == before["pending"]
    assert observed["commission"] == before["commission"]
    assert observed["assist"] == before["assist"]
    assert actor["pending_tasks"] == pending
    assert asyncio.run(deliver(replace(event, event_type="edit", server_event_at=NOW + 2, text=complete)))
    assert not env.owner["wanxin_observation"]["pending"]
    assert not actor["pending_tasks"]
    completed = copy.deepcopy(env.owner["wanxin_observation"])
    assert asyncio.run(deliver(replace(event, event_type="edit", server_event_at=NOW + 2, text=complete)))
    assert env.owner["wanxin_observation"] == completed


def test_identification_named_helper_must_match_original_command_actor(env):
    event = asyncio.run(start_owned(env, "identify"))
    before = copy.deepcopy(env.owner)
    assert not asyncio.run(deliver(replace(event, text=IDENTIFIED.replace("@helper", "@somebody"))))
    assert env.owner == before


def test_identification_helper_alias_is_still_a_valid_original_actor(env):
    event = asyncio.run(start_owned(env, "identify"))
    state_module.update_send_as_profile(HELPER, username="helper_new")
    assert asyncio.run(deliver(replace(event, text=IDENTIFIED)))
    assert env.owner["wanxin_observation"]["assist"]["identified_commission_id"] == 5


def test_publication_without_id_does_not_repeat_after_timeout_and_day_rollover(env):
    observed = env.owner["wanxin_observation"]
    observed["auto_config"].update(
        publish_enabled=True, visit_enabled=False, protect_enabled=False, deduce_enabled=False, moon_greet_enabled=False,
    )
    event = asyncio.run(start_owned(env, "publish"))
    assert not asyncio.run(deliver(replace(event, text=PUBLISH)))
    for now in (NOW + 100, NOW + 1000, NOW + 90000):
        env.monkeypatch.setattr(wanxin.time, "time", lambda: now)
        asyncio.run(schedule(now))
    assert env.owner["wanxin_observation"]["unresolved_actions"]["publish"]["msg_id"] == 100
    wanxin.send_game_command.assert_awaited_once()


@pytest.mark.parametrize("action,text", [
    ("publish", PUBLISHED), ("accept", ACCEPT + "\n" + ACCEPT_BODY),
    ("identify", IDENTIFIED), ("cancel", NO_CANCEL),
])
def test_sqlite_failure_rolls_back_owner_and_original_actor_pending(persistent_env, action, text):
    env = persistent_env
    event = asyncio.run(start_owned(env, action))
    assert persistence.save_state()
    before = copy.deepcopy(env.owner)
    actor_before = copy.deepcopy(state_module.get_identity_state(event.identity_id)["pending_tasks"])
    conn = persistence.get_db_conn()
    conn.execute(f"CREATE TEMP TRIGGER fail_wanxin BEFORE INSERT ON identity_runtime_state "
                 f"WHEN NEW.send_as_id = {OWNER} BEGIN SELECT RAISE(ABORT, 'wanxin failure'); END")
    try:
        assert not asyncio.run(deliver(replace(event, text=text)))
        assert env.owner == before
        assert state_module.get_identity_state(event.identity_id)["pending_tasks"] == actor_before
    finally:
        conn.execute("DROP TRIGGER fail_wanxin")
    assert asyncio.run(deliver(replace(event, text=text)))
    assert not env.owner["wanxin_observation"]["pending"]
    assert not state_module.get_identity_state(event.identity_id)["pending_tasks"]


@pytest.mark.parametrize("action,partial,complete", [
    ("publish", PUBLISH, PUBLISHED), ("identify", IDENTIFY, IDENTIFIED),
])
def test_sqlite_reload_preserves_unknown_then_adopts_the_original_final_edit(persistent_env, action, partial, complete):
    env = persistent_env
    event = asyncio.run(start_owned(env, action))
    assert not asyncio.run(deliver(replace(event, text=partial)))
    before = copy.deepcopy(env.owner["wanxin_observation"]["pending"])
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.owner = state_module.get_identity_state(OWNER)
    assert env.owner["wanxin_observation"]["pending"] == before
    assert asyncio.run(deliver(replace(event, event_type="edit", server_event_at=NOW + 2, text=complete)))
    completed = copy.deepcopy(env.owner["wanxin_observation"])
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.owner = state_module.get_identity_state(OWNER)
    assert asyncio.run(deliver(replace(event, event_type="edit", server_event_at=NOW + 3, text=complete)))
    assert env.owner["wanxin_observation"]["commission"] == completed["commission"]
    assert env.owner["wanxin_observation"]["assist"] == completed["assist"]
