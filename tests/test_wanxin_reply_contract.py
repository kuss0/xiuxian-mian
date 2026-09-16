import asyncio
import copy
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from model import persistence, state as state_module
from model.features import wanxin
from model.real_message_replay import iter_real_message_samples
from test_wanxin_lifecycle import CHAT, NOW, OWNER, deliver, receipt
from test_wanxin_lifecycle import env as env
from test_wanxin_liveness import persistent_env as persistent_env
from yinluo_native_support import native_logs, native_reply


SAMPLES = {
    sample.family: sample.text
    for sample in iter_real_message_samples(
        Path(__file__).parent / "fixtures" / "real_message_samples.json", module="wanxin",
    )
}
HEADERS = {
    action: SAMPLES[wanxin.WANXIN_ACTION_FAMILIES[action]].splitlines()[0]
    for action in ("status", "moon_status", "visit", "protect", "deduce", "moon_greet", "moon_seal")
}
HEADERS["moon_join"] = "\u3010\u6708\u4e0b\u5408\u53c2\u3011"
PROCESSING = "\u6b63\u5728\u5904\u7406\uff0c\u8bf7\u7a0d\u5019\u3002"
GAIN = "\u60c5\u7f18 +9\u3002"
COST = "\u6d88\u8017\uff1a500\u4fee\u4e3a\u300124\u60c5\u7f18\u3002"
VISIT_FAILED = "\u63a2\u671b\u5357\u5bab\u5a49\u5931\u8d25\uff0c\u672a\u80fd\u7a33\u4f4f\u5979\u7684\u795e\u9b42\u3002"
PROTECT_FAILED = "\u62a4\u6301\u795e\u9b42\u5931\u8d25\uff0c\u4fee\u4e3a\u4e0d\u8db3\u3002"
ALREADY = "\u4eca\u65e5\u5df2\u4e0e\u5a49\u5f71\u95ee\u5b89\u3002"
WAIT = "\u8bf7\u5728 2\u5c0f\u65f6 \u540e\u518d\u5a49\u5f71\u95ee\u5b89\u3002"
PANEL_FIELDS = ("\u5a49\u5fc3", "\u9b42\u5c01", "\u6708\u9b44", "\u5492\u6e90")
NUMERIC_PANEL = "\n".join(f"{label}\uff1a{value}" for label, value in zip(PANEL_FIELDS, (120, 0, 18, 120)))


def parse(action, text):
    return wanxin.parse_wanxin_text(text, now=NOW, family=wanxin.WANXIN_ACTION_FAMILIES[action])


@pytest.mark.parametrize("action", HEADERS)
@pytest.mark.parametrize("suffix", ["", "\n" + PROCESSING])
def test_heading_or_processing_text_is_not_a_terminal_result(action, suffix):
    assert parse(action, HEADERS[action] + suffix)["type"] == "unknown"


@pytest.mark.parametrize("action,text", [
    ("visit", VISIT_FAILED),
    ("protect", PROTECT_FAILED),
    ("moon_greet", HEADERS["moon_greet"] + "\n" + GAIN + "\n" + PROCESSING),
    ("moon_greet", HEADERS["moon_greet"] + "\n" + GAIN + "\n" + ALREADY),
    ("moon_greet", HEADERS["moon_greet"] + "\n" + GAIN + "\n" + WAIT),
    ("moon_seal", HEADERS["moon_seal"] + "\n" + COST + "\n" + PROTECT_FAILED),
    ("visit", "\u8bf7\u5148\u63a2\u671b\u5357\u5bab\u5a49\u3002"),
    ("protect", "\u62a4\u6301\u795e\u9b42\u9700\u8981 800 \u4fee\u4e3a\u3002"),
])
def test_negative_mixed_or_instructional_text_is_not_success(action, text):
    assert parse(action, text)["type"] == "unknown"


@pytest.mark.parametrize("action,body", [
    ("moon_greet", GAIN + "\n" + GAIN),
    ("moon_greet", GAIN.replace("+9", "+9.5")),
    ("moon_greet", GAIN.replace("+9", "+9foo")),
    ("moon_greet", GAIN.replace("+9", "+\uff19")),
    ("moon_greet", GAIN.replace("+9", "+" + str(2 ** 63))),
    ("moon_seal", COST + "\n" + COST),
    ("moon_seal", COST.replace("24", "-24")),
    ("moon_seal", COST.replace("24", "24.5")),
    ("moon_seal", COST.replace("24", str(2 ** 63))),
    ("deduce", "\u5492\u6e90 +16.5\u3002"),
    ("deduce", "\u5492\u6e90 +16\u3002\n\u5492\u6e90 +20\u3002"),
])
def test_action_numbers_are_explicit_unique_complete_bounded_integers(action, body):
    assert parse(action, HEADERS[action] + "\n" + body)["type"] == "unknown"


@pytest.mark.parametrize("actions", [
    ("moon_greet", "moon_seal"), ("visit", "protect"), ("deduce", "visit"),
    ("status", "moon_status"), ("moon_greet", "moon_greet"),
])
def test_concatenated_outcomes_do_not_select_the_first_title(actions):
    text = "\n".join(SAMPLES[wanxin.WANXIN_ACTION_FAMILIES[action]] for action in actions)
    assert parse(actions[0], text)["type"] == "unknown"


@pytest.mark.parametrize("value", ["", "-1", "+1", "12.5", "12foo", "\uff11\uff12", "NaN", str(2 ** 63)])
def test_malformed_absolute_panel_cannot_be_partially_read(value):
    text = HEADERS["status"] + "\n" + NUMERIC_PANEL.replace("\u9b42\u5c01\uff1a0", "\u9b42\u5c01\uff1a" + value)
    parsed = parse("status", text)
    assert parsed["type"] == "unknown"
    assert not parsed["values"]


@pytest.mark.parametrize("field", PANEL_FIELDS)
def test_partial_or_duplicate_panel_does_not_calibrate_missing_fields(field):
    lines = NUMERIC_PANEL.splitlines()
    for body in (
        "\n".join(line for line in lines if not line.startswith(field)),
        NUMERIC_PANEL + f"\n{field}\uff1a0",
    ):
        parsed = parse("status", HEADERS["status"] + "\n" + body)
        assert parsed["type"] == "unknown"
        assert not parsed["values"]


@pytest.mark.parametrize("value", ["", "12.5", "-1", "\uff11\uff12", str(2 ** 63)])
def test_moon_panel_requires_an_unambiguous_absolute_affinity(value):
    text = SAMPLES["wanxin_moon_panel"].replace("\u60c5\u7f18\uff1a314", "\u60c5\u7f18\uff1a" + value)
    assert parse("moon_status", text)["type"] == "unknown"


def test_duplicate_moon_affinity_is_not_last_or_first_wins():
    text = SAMPLES["wanxin_moon_panel"] + "\n\u60c5\u7f18\uff1a999"
    assert parse("moon_status", text)["type"] == "unknown"


def test_heading_does_not_override_explicit_cooldown_or_join_refusal():
    cooldown = parse("moon_greet", HEADERS["moon_greet"] + "\n" + WAIT)
    assert (cooldown["type"], cooldown["cooldown_action"]) == ("cooldown", "moon_greet")
    assert cooldown["next_time"] == NOW + 7200 + wanxin.CD_BUFFER_SEC
    blocked = parse("moon_join", HEADERS["moon_join"] + "\n"
                    "\u5c01\u9b42\u5492\u5c1a\u672a\u89e3\u9664\uff0c\u6708\u4e0b\u5408\u53c2\u4e0d\u53ef\u8d38\u7136\u65bd\u5c55\u3002")
    assert blocked["type"] == "moon_join_blocked"


def test_explicit_zero_and_complete_absolute_zero_panel_are_not_missing_values():
    parsed = parse("moon_greet", HEADERS["moon_greet"] + "\n" + GAIN.replace("+9", "+0"))
    assert (parsed["type"], parsed["affinity_gain"]) == ("moon_greet_success", 0)
    panel = parse("status", HEADERS["status"] + "\n"
                  + "\n".join(f"{label}\uff1a0" for label in PANEL_FIELDS))
    assert panel["type"] == "panel"
    assert panel["values"] == {"wanxin": 0, "soul_seal": 0, "moon_soul": 0, "curse_source": 0}


@pytest.mark.parametrize("action,body", [
    ("moon_greet", "\u793a\u4f8b\uff1a" + ALREADY),
    ("moon_greet", "\u5c1a\u672a\u786e\u8ba4" + ALREADY),
    ("moon_greet", HEADERS["moon_seal"] + "\n" + ALREADY),
    ("visit", "\u5c1a\u672a\u786e\u8ba4\u4eca\u65e5\u5df2\u63a2\u671b\u8fc7\u5357\u5bab\u5a49\u3002"),
    ("visit", HEADERS["protect"] + "\n\u4eca\u65e5\u5df2\u63a2\u671b\u8fc7\u5357\u5bab\u5a49\u3002"),
    ("protect", "\u62a4\u6301\u795e\u9b42\u6210\u529f\uff0c"),
    ("protect", HEADERS["protect"] + "\n\u62a4\u6301\u795e\u9b42\u6210\u529f\uff0c\u9b42\u5c01 -5.5\u3002"),
])
def test_refusal_and_short_success_phrases_require_their_own_result_context(action, body):
    assert parse(action, body)["type"] == "unknown"


@pytest.mark.parametrize("body", [
    GAIN + "\n\u672a\u6210\u529f\u95ee\u5b89\u3002",
    GAIN + "\n\u8fd9\u53ea\u662f\u9884\u89c8\u3002",
    GAIN + "\n\u4e0d\u53ef\u65bd\u5c55\u3002",
])
def test_negative_or_preview_suffix_cannot_complete_a_greeting(body):
    assert parse("moon_greet", HEADERS["moon_greet"] + "\n" + body)["type"] == "unknown"


@pytest.mark.parametrize("duration", ["0\u79d2", "-1\u5c0f\u65f6", "1.5\u5c0f\u65f6", "1\u5c0f\u65f61\u5c0f\u65f6",
                                      "\uff11\u5c0f\u65f6", "999999\u5c0f\u65f6", "\u672a\u77e5"])
def test_malformed_cooldown_does_not_consume_an_owned_action(duration):
    text = HEADERS["moon_greet"] + "\n" + WAIT.replace("2\u5c0f\u65f6", duration)
    assert parse("moon_greet", text)["type"] == "unknown"


def test_ambiguous_cooldown_action_and_duplicate_waits_stay_unknown():
    for text in (
        HEADERS["moon_greet"] + "\n" + WAIT + "\n" + WAIT,
        HEADERS["protect"] + "\n" + WAIT,
    ):
        assert parse("protect", text)["type"] == "unknown"


@pytest.mark.parametrize("body", [
    SAMPLES["wanxin_moon_panel"].replace("\u5171\u9e23\uff1a\u5df2\u89c9\u9192", "\u5171\u9e23\uff1a\u672a\u89c9\u9192"),
    SAMPLES["wanxin_moon_panel"] + "\n\u5171\u9e23\uff1a\u5df2\u89c9\u9192",
    SAMPLES["wanxin_moon_panel"] + "\n\u4f8d\u59be\uff1a\u3010\u5357\u5bab\u5a49\u00b7\u6708\u5f71\u3011",
    SAMPLES["wanxin_moon_panel"].replace("\u5357\u5bab\u5a49\u00b7\u6708\u5f71", "\u5176\u4ed6\u4eba"),
])
def test_moon_panel_does_not_infer_partner_or_awakening_from_the_title(body):
    assert parse("moon_status", body)["type"] == "unknown"


def test_only_absolute_panel_fields_not_embedded_prose_establish_calibration():
    text = HEADERS["moon_greet"] + "\n" + GAIN + "\n\u4ecb\u7ecd\uff1a\u5a49\u5fc3 1 | \u9b42\u5c01 1"
    assert parse("moon_greet", text)["type"] == "unknown"
    no_panel = parse("moon_greet", HEADERS["moon_greet"] + "\n" + GAIN + "\n\u4ecb\u7ecd\uff1a\u5a49\u5fc3 1")
    assert no_panel["type"] == "moon_greet_success"
    assert not no_panel["values"]


@pytest.mark.parametrize("action,body", [
    ("visit", "\u4f60\u63a2\u671b\u5357\u5bab\u5a49\uff0c\u5a49\u5fc3\u5fae\u52a8\uff0c\u5c01\u9b42\u7a0d\u7f13\u3002"),
    ("protect", "\u62a4\u6301\u795e\u9b42\u6210\u529f\uff0c\u9b42\u5c01 -5\u3002"),
])
def test_headerless_legacy_success_cannot_be_borrowed_under_another_action_title(action, body):
    assert parse(action, body)["type"] == action + "_success"
    assert parse(action, HEADERS["moon_join"] + "\n" + body)["type"] == "unknown"


def test_awakened_panel_requires_its_actual_acknowledgement_not_an_example():
    text = ("\u3010\u5a49\u5f71\u89c9\u9192\u3011\n"
            "\u793a\u4f8b\uff1a\u3010\u5357\u5bab\u5a49\u3011\u5df2\u89c9\u9192\u4e3a \u3010\u5357\u5bab\u5a49\u00b7\u6708\u5f71\u3011\u3002\n"
            + NUMERIC_PANEL)
    assert parse("moon_status", text)["type"] == "unknown"


def test_protection_expense_proof_is_bounded_even_when_not_booked_here():
    text = SAMPLES["wanxin_protect"].replace("800", str(2 ** 63))
    assert parse("protect", text)["type"] == "unknown"


@pytest.mark.parametrize("body", [
    GAIN + "\n\u5c1a\u672a\u95ee\u5b89\u3002", GAIN + "\n\u672a\u6267\u884c\u95ee\u5b89\u3002",
])
def test_explicit_non_execution_suffix_cannot_be_overridden_by_a_delta(body):
    assert parse("moon_greet", HEADERS["moon_greet"] + "\n" + body)["type"] == "unknown"


async def start_owned(env, action):
    sender = AsyncMock(return_value=receipt())
    env.monkeypatch.setattr(wanxin, "send_game_command", sender)
    with state_module.use_identity(OWNER):
        observed = wanxin.normalize_wanxin_observation(env.owner["wanxin_observation"])
        assert await wanxin._send_nonfinancial_action(
            observed, action, wanxin.WANXIN_ACTION_COMMANDS[action], NOW, send_as_id=OWNER,
        )
    pending = copy.deepcopy(env.owner["wanxin_observation"]["pending"])
    env.owner["pending_tasks"][(CHAT, 100)] = {
        "cmd": pending["command"], "family": pending["family"], "chat_id": CHAT,
        "account_id": OWNER, "sent_at": NOW, "op_id": pending["op_id"],
    }
    return pending


@pytest.mark.parametrize("action", HEADERS)
def test_owned_partial_reply_preserves_pending_and_all_business_fields(env, action):
    env.owner["concubine_affinity"] = 200
    asyncio.run(start_owned(env, action))
    before = copy.deepcopy(env.owner)
    event = native_reply(OWNER, wanxin.WANXIN_ACTION_COMMANDS[action], HEADERS[action], NOW + 1, root=100, command_at=NOW)
    assert not asyncio.run(deliver(event))
    assert env.owner["concubine_affinity"] == before["concubine_affinity"]
    assert env.owner["pending_tasks"] == before["pending_tasks"]
    for key, value in before["wanxin_observation"].items():
        if key not in {"reply_points", "auto_last_error"}:
            assert env.owner["wanxin_observation"][key] == value, key


@pytest.mark.parametrize("action", ["visit", "protect", "deduce", "moon_greet", "moon_seal"])
def test_final_edit_after_partial_reply_and_reload_completes_once(env, action):
    env.owner["concubine_affinity"] = 200
    original = asyncio.run(start_owned(env, action))
    event = native_reply(OWNER, original["command"], HEADERS[action], NOW + 1, root=100, command_at=NOW)
    assert not asyncio.run(deliver(event))
    env.owner["wanxin_observation"] = json.loads(json.dumps(env.owner["wanxin_observation"]))
    completed = replace(event, text=SAMPLES[original["family"]], event_type="edit", server_event_at=NOW + 2)
    assert asyncio.run(deliver(completed))
    assert not env.owner["wanxin_observation"]["pending"]
    assert not env.owner["pending_tasks"]
    before = copy.deepcopy(env.owner)
    assert asyncio.run(deliver(completed))
    assert env.owner == before


@pytest.mark.parametrize("intermediate", [HEADERS["moon_greet"], HEADERS["moon_greet"] + "\n" + WAIT, ALREADY])
def test_incomplete_or_type_changing_edit_does_not_erase_a_completed_delta(env, intermediate):
    env.owner["concubine_affinity"] = 120
    event = native_reply(OWNER, wanxin.CMD_WANXIN_MOON_GREET, HEADERS["moon_greet"] + "\n" + GAIN,
                         NOW + 1, root=100, command_at=NOW)
    assert asyncio.run(deliver(event))
    before = copy.deepcopy(env.owner)
    assert not asyncio.run(deliver(replace(event, event_type="edit", server_event_at=NOW + 2, text=intermediate)))
    assert env.owner == before
    env.owner["wanxin_observation"] = json.loads(json.dumps(env.owner["wanxin_observation"]))
    assert asyncio.run(deliver(replace(event, event_type="edit", server_event_at=NOW + 3)))
    assert env.owner["concubine_affinity"] == 129
    assert env.owner["wanxin_observation"]["next_moon_greet_time"] == before["wanxin_observation"]["next_moon_greet_time"]


@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("failure", [False, RuntimeError("save failed")])
def test_reply_save_failure_keeps_pending_affinity_and_prior_evidence(env, complete, failure):
    env.owner["concubine_affinity"] = 200
    asyncio.run(start_owned(env, "moon_greet"))
    before = copy.deepcopy(env.owner)
    text = HEADERS["moon_greet"] + ("\n" + GAIN if complete else "")
    event = native_reply(OWNER, wanxin.CMD_WANXIN_MOON_GREET, text, NOW + 1, root=100, command_at=NOW)
    env.monkeypatch.setattr(wanxin, "save_state", Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=False))
    if isinstance(failure, Exception):
        with pytest.raises(RuntimeError, match="save failed"):
            asyncio.run(deliver(event))
    else:
        assert not asyncio.run(deliver(event))
    assert env.owner == before


@pytest.mark.parametrize("intermediate", [
    HEADERS["moon_greet"],
    HEADERS["moon_greet"] + "\n" + GAIN + "\n\u5a49\u5fc3\uff1a1",
])
def test_sqlite_roundtrip_keeps_completed_delta_through_incomplete_edits(persistent_env, intermediate):
    env = persistent_env
    env.owner["concubine_affinity"] = 120
    event = native_reply(OWNER, wanxin.CMD_WANXIN_MOON_GREET, HEADERS["moon_greet"] + "\n" + GAIN,
                         NOW + 1, root=100, command_at=NOW)
    assert asyncio.run(deliver(event))
    due = env.owner["wanxin_observation"]["next_moon_greet_time"]
    assert not asyncio.run(deliver(replace(event, event_type="edit", server_event_at=NOW + 2, text=intermediate)))
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    env.owner = state_module.get_identity_state(OWNER)
    assert asyncio.run(deliver(replace(event, event_type="edit", server_event_at=NOW + 3)))
    assert env.owner["concubine_affinity"] == 129
    assert env.owner["wanxin_observation"]["next_moon_greet_time"] == due


def test_log_replay_while_disabled_retains_partial_then_finishes_original_edit(env):
    asyncio.run(start_owned(env, "moon_greet"))
    env.owner["concubine_affinity"] = 120
    env.owner["wanxin_enabled"] = False
    event = native_reply(OWNER, wanxin.CMD_WANXIN_MOON_GREET, HEADERS["moon_greet"], NOW + 1, root=100, command_at=NOW)
    rows = native_logs(event)
    env.monkeypatch.setattr(wanxin, "_iter_message_log_entries_between", lambda *_args: iter((row, row["server_event_at"]) for row in rows))
    asyncio.run(wanxin.run_wanxin_global_cleanup_scheduler(NOW + 100))
    assert "moon_greet" in env.owner["wanxin_observation"]["unresolved_actions"]
    assert env.owner["concubine_affinity"] == 120
    completed = replace(event, event_type="edit", server_event_at=NOW + 2, text=event.text + "\n" + GAIN)
    rows = native_logs(event, completed)
    asyncio.run(wanxin.run_wanxin_global_cleanup_scheduler(NOW + 1000))
    assert not env.owner["wanxin_observation"]["unresolved_actions"]
    assert not env.owner["pending_tasks"]
    assert env.owner["concubine_affinity"] == 129
    assert not env.owner["wanxin_enabled"]
    wanxin.send_game_command.assert_awaited_once()
