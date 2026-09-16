import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import concubine, passive_inbox


ID, ROOT, NOW = 99085001, 85001, 1_700_000_500.0
NAME = "\u51cc\u7389\u7075"
HEAD = f"\u4f8d\u59be\u3010{NAME}\u3011\uff08\u968f\u884c\u4e2d\uff09\u7684\u6b8b\u56fe\u5377\u8f74\u5982\u4e0b\uff1a\n"
PIECES = {
    "xutian": ("\u5317\u9619\u6b8b\u7eb9", "\u5357\u6e0a\u6b8b\u7eb9", "\u4e1c\u79bb\u6b8b\u7eb9", "\u897f\u6781\u6b8b\u7eb9"),
    "cangkun": ("\u6155\u5170\u6b8b\u7eb9", "\u7981\u95e8\u6b8b\u7eb9", "\u7389\u5323\u6b8b\u7eb9", "\u592a\u5999\u6b8b\u7eb9"),
}
NONE = "\u65e0"


def section(kind, count):
    label = concubine.FRAGMENT_LABELS[kind]
    collected = "\u3001".join(PIECES[kind][:count]) or NONE
    missing = "\u3001".join(PIECES[kind][count:]) or NONE
    return (f"\u3010{label}\u6b8b\u56fe\u5377\u3011\n\u62fc\u7247\u8fdb\u5ea6\uff1a{count}/4\n"
            f"\u5df2\u6536\u96c6\uff1a{collected}\n\u7f3a\u5931\u6b8b\u7eb9\uff1a{missing}\n\u91cd\u590d\u85cf\u672c\uff1a{NONE}\n")


def panel(xutian=3, cangkun=4):
    return HEAD + section("xutian", xutian) + "\n" + section("cangkun", cangkun)


@pytest.fixture
def env(monkeypatch, request):
    before = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_game_group_id(-100850001)
    state_module.set_game_bot_ids([88085001])
    state_module.set_identity_account(ID, 8501)
    identity = state_module.get_identity_state(ID)
    identity.update(concubine_enabled=True, concubine_availability="available", concubine_name=getattr(request, "param", NAME),
                    concubine_affinity=1000, concubine_phase="idle", concubine_fragment_msg_id=0,
                    concubine_dream_due_at=NOW + 3600)
    send = AsyncMock(return_value=SimpleNamespace(id=ROOT, chat_id=-100850001, sent_at=NOW, send_started_at=NOW))
    save = Mock(return_value=True)
    monkeypatch.setattr(concubine, "_CONCUBINE_QUERY_INFLIGHT", {})
    monkeypatch.setattr(concubine, "send_game_command", send)
    monkeypatch.setattr(concubine, "save_state", save)
    monkeypatch.setattr(concubine, "mark_dirty", Mock())
    monkeypatch.setattr(concubine, "console_log", Mock())
    monkeypatch.setattr(concubine, "send_audit_log", AsyncMock())
    monkeypatch.setattr(concubine.time, "time", lambda: NOW)
    monkeypatch.setattr(concubine.random, "uniform", lambda *_args: 120)
    monkeypatch.setattr(passive_inbox, "save_state", save)
    monkeypatch.setattr(passive_inbox, "_record_passive_event", Mock())
    monkeypatch.setattr(passive_inbox, "_observed_passive_events", {})
    try:
        with state_module.use_identity(ID):
            concubine._set_fragment_progress("xutian", 4, 4)
            concubine._set_fragment_progress("cangkun", 4, 4)
            assert asyncio.run(concubine._send_fragment_command(NOW))
            send.reset_mock()
            save.reset_mock()
            yield SimpleNamespace(identity=identity, send=send, save=save)
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(before)


def reply(text):
    return asyncio.run(concubine.handle_concubine_fragment_reply(
        text, NOW, SimpleNamespace(id=ROOT, chat_id=-100850001, raw_text=concubine.CMD_CONCUBINE_FRAGMENT),
        matched_family="concubine_fragment", current_msg_id=ROOT + 1, current_chat_id=-100850001,
        observed_at=NOW, reply_context={"sender_id": 88085001}))


@pytest.mark.parametrize("text", [
    "\u3010\u865a\u5929\u6b8b\u56fe\u5377\u3011",
    HEAD + section("xutian", 4),
    HEAD + section("cangkun", 4),
    "working",
])
def test_partial_read_cannot_confirm_cached_complete_fragments(env, text):
    assert concubine._confirmed_completed_fragment_kinds_from_reply(text) == []


@pytest.mark.parametrize("change", [
    "partial", "missing_progress", "missing_missing", "missing_collected", "duplicate_heading",
    "duplicate_progress", "duplicate_missing", "overfull", "zero_total", "wrong_total",
    "contradictory_missing", "contradictory_collected", "duplicate_piece", "partner",
])
def test_incomplete_or_conflicting_panel_preserves_pending_and_all_counts(env, change):
    text = panel()
    if change == "partial":
        text = HEAD + section("xutian", 3)
    elif change == "missing_progress":
        text = text.replace("\u62fc\u7247\u8fdb\u5ea6\uff1a4/4\n", "")
    elif change == "missing_missing":
        text = text.replace(f"\u7f3a\u5931\u6b8b\u7eb9\uff1a{NONE}\n", "")
    elif change == "missing_collected":
        text = text.replace("\u5df2\u6536\u96c6\uff1a" + "\u3001".join(PIECES["cangkun"]) + "\n", "")
    elif change == "duplicate_heading":
        text += "\n" + section("cangkun", 4)
    elif change == "duplicate_progress":
        text += "\u62fc\u7247\u8fdb\u5ea6\uff1a3/4\n"
    elif change == "duplicate_missing":
        text += f"\u7f3a\u5931\u6b8b\u7eb9\uff1a{NONE}\n"
    elif change in {"overfull", "zero_total", "wrong_total"}:
        text = text.replace("4/4", {"overfull": "5/4", "zero_total": "4/0", "wrong_total": "4/5"}[change])
    elif change == "contradictory_missing":
        text = text.replace(f"\u7f3a\u5931\u6b8b\u7eb9\uff1a{NONE}", f"\u7f3a\u5931\u6b8b\u7eb9\uff1a{PIECES['cangkun'][0]}")
    elif change == "contradictory_collected":
        text = text.replace("\u5df2\u6536\u96c6\uff1a" + "\u3001".join(PIECES["cangkun"]), f"\u5df2\u6536\u96c6\uff1a{NONE}")
    elif change == "duplicate_piece":
        text = text.replace(PIECES["cangkun"][0], PIECES["cangkun"][1])
    else:
        text = text.replace(NAME, "replacement")
    before = copy.deepcopy(env.identity)
    assert not reply(text)
    assert env.identity == before
    env.save.assert_not_called()


@pytest.mark.parametrize("phase", ["fragment_pending", "puzzle_pending"])
def test_timeout_revokes_confirmation_without_inventing_missing_fragments(env, phase):
    concubine._mark_fragment_confirmation(NOW - 1)
    before = {key: env.identity[key] for keys in concubine.FRAGMENT_FIELDS.values() for key in keys}
    concubine._backoff_after_pending_timeout(NOW, phase)
    assert all(env.identity[key] == value for key, value in before.items())
    assert env.identity["concubine_fragment_confirm_key"] == ""
    assert env.identity["concubine_fragment_confirmed_at"] == 0


def test_puzzle_requires_current_fragment_confirmation(env):
    env.identity.update(concubine_phase="puzzle_ready", concubine_fragment_msg_id=0, concubine_status_query={})
    assert not asyncio.run(concubine._send_puzzle_command(NOW))
    env.send.assert_not_awaited()


def test_legacy_puzzle_ready_without_confirmation_requeries_fragments(env):
    env.identity.update(concubine_phase="puzzle_ready", concubine_fragment_msg_id=0, concubine_status_query={}, next_concubine_time=NOW)
    asyncio.run(concubine.run_concubine_scheduler(NOW))
    env.send.assert_awaited_once()
    assert env.send.await_args.args[0] == concubine.CMD_CONCUBINE_FRAGMENT


@pytest.mark.parametrize("xutian", range(5))
@pytest.mark.parametrize("cangkun", range(5))
def test_complete_panel_overwrites_only_explicit_progress_and_confirms_full_kinds(env, xutian, cangkun):
    assert reply(panel(xutian, cangkun))
    assert concubine._get_fragment_progress("xutian") == (xutian, 4)
    assert concubine._get_fragment_progress("cangkun") == (cangkun, 4)
    expected = "|".join(f"{kind}:4/4" for kind, count in (("xutian", xutian), ("cangkun", cangkun)) if count == 4)
    assert env.identity["concubine_fragment_confirm_key"] == expected
    assert env.identity["concubine_phase"] == ("puzzle_ready" if expected else "idle")


@pytest.mark.parametrize("text", [HEAD + section("xutian", 2), panel()])
def test_generic_passive_fragment_fallback_cannot_bypass_the_panel_reducer(env, text):
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(passive_inbox.handle_passive_module_card(
        text, now=NOW, reply_context={"send_as_id": ID, "family": "concubine_fragment"},
        event=SimpleNamespace(id=ROOT + 1, chat_id=-100850001, sender_id=88085001, server_event_at=NOW),
        event_type="message",
    ))
    assert env.identity == before


@pytest.mark.parametrize("explicit_identity", [False, True])
def test_passive_fragment_reply_reaches_the_same_pending_panel_reducer(env, explicit_identity):
    context = {"family": "concubine_fragment", "root_msg_id": ROOT, "reply_to_msg_id": ROOT,
               "reply_to_command": concubine.CMD_CONCUBINE_FRAGMENT}
    if explicit_identity:
        context["send_as_id"] = ID
    event = SimpleNamespace(id=ROOT + 1, chat_id=-100850001, sender_id=88085001, server_event_at=NOW)
    assert asyncio.run(passive_inbox.handle_passive_module_card(
        panel(), now=NOW, reply_context=context, event=event, event_type="message"))
    assert env.identity["concubine_phase"] == "puzzle_ready"
    assert env.identity["concubine_fragment_confirm_key"] == "cangkun:4/4"
    assert env.identity["concubine_fragment_msg_id"] == 0
    assert env.identity["concubine_dream_due_at"] == NOW + 3600


@pytest.mark.parametrize("env", ["\u54b8\u9c7c"], indirect=True)
def test_recorded_game_scroll_matches_the_strict_panel_contract(env):
    samples = json.loads((Path(__file__).parent / "fixtures" / "real_message_samples.json").read_text(encoding="utf-8"))
    text = samples["concubine.fragment.scroll"]["text"]
    parsed = concubine._parse_fragment_panel(text)
    assert parsed == {"partner": "\u54b8\u9c7c", "progresses": {"xutian": (4, 4), "cangkun": (2, 4)}}
    assert reply(text)
    assert env.identity["concubine_fragment_confirm_key"] == "xutian:4/4"


@pytest.mark.parametrize("variant", ["crlf", "colon", "indent", "reverse"])
def test_complete_panel_preserves_supported_whitespace_and_section_order(env, variant):
    text = panel()
    if variant == "crlf":
        text = text.replace("\n", "\r\n")
    elif variant == "colon":
        text = text.replace("\uff1a", ":")
    elif variant == "indent":
        text = "\n".join("  " + line + "  " for line in text.splitlines())
    else:
        text = HEAD + section("cangkun", 4) + "\n" + section("xutian", 3)
    assert reply(text)
    assert env.identity["concubine_fragment_confirm_key"] == "cangkun:4/4"


def test_collected_and_missing_piece_sets_cannot_overlap(env):
    text = panel().replace(f"\u7f3a\u5931\u6b8b\u7eb9\uff1a{PIECES['xutian'][3]}",
                           f"\u7f3a\u5931\u6b8b\u7eb9\uff1a{PIECES['xutian'][0]}")
    before = copy.deepcopy(env.identity)
    assert not reply(text)
    assert env.identity == before


@pytest.mark.parametrize("root", [ROOT + 10, 0])
def test_passive_fragment_cannot_use_an_unrelated_or_missing_root(env, root):
    context = {"family": "concubine_fragment", "root_msg_id": root, "reply_to_msg_id": root, "send_as_id": ID}
    event = SimpleNamespace(id=ROOT + 20, chat_id=-100850001, sender_id=88085001, server_event_at=NOW)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(passive_inbox.handle_passive_module_card(
        panel(), now=NOW, reply_context=context, event=event, event_type="message"))
    assert env.identity == before


def test_complete_edit_after_partial_passive_panel_can_finish_pending_read(env):
    context = {"family": "concubine_fragment", "root_msg_id": ROOT, "reply_to_msg_id": ROOT, "send_as_id": ID}
    event = SimpleNamespace(id=ROOT + 1, chat_id=-100850001, sender_id=88085001, server_event_at=NOW)
    assert not asyncio.run(passive_inbox.handle_passive_module_card(
        HEAD + section("xutian", 4), now=NOW, reply_context=context, event=event, event_type="message"))
    assert env.identity["concubine_phase"] == "fragment_pending"
    assert asyncio.run(passive_inbox.handle_passive_module_card(
        panel(), now=NOW, reply_context=context, event=event, event_type="edit"))
    assert env.identity["concubine_phase"] == "puzzle_ready"


@pytest.mark.parametrize("phase,root", [("idle", ROOT), ("puzzle_pending", ROOT), ("fragment_pending", ROOT + 1)])
def test_legacy_unowned_fragment_reply_is_not_reported_as_completed(env, phase, root):
    env.identity["concubine_phase"] = phase
    env.identity["concubine_status_query"] = {}
    assert not asyncio.run(concubine.handle_concubine_fragment_reply(
        panel(), NOW, SimpleNamespace(id=root, raw_text=concubine.CMD_CONCUBINE_FRAGMENT),
        matched_family="concubine_fragment", current_msg_id=ROOT + 2, current_chat_id=-100850001,
        observed_at=NOW, reply_context={"sender_id": 88085001}))
