import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import concubine, passive_inbox


NOW = 1_700_000_500.0
ID = 991101
CHAT = -1001680975844
BOT = 991102
NAME = "\u51cc\u7389\u7075"
HEAD = f"\u4f60\u7684\u9053\u5fc3\u4f8d\u59be: \u3010{NAME}\u3011 (\u72b6\u6001: \u968f\u884c\u4e2d)"
FIELDS = {
    "affinity": "\u60c5\u7f18\u503c: 184",
    "dream_due_at": "\u5165\u68a6\u5bfb\u56fe\u51b7\u5374: 2\u5c0f\u65f6",
    "tianji_due_at": "\u5929\u673a\u4ee3\u535c\u51b7\u5374: 3\u5c0f\u65f6",
    "heart_due_at": "\u5171\u5386\u5fc3\u52ab\u51b7\u5374: 4\u5c0f\u65f6",
}
PANEL = "\n".join([HEAD, *FIELDS.values()])
OPTIONAL = {
    "oath": "\u5f53\u524d\u8a93\u7ea6: \u65e0",
    "tianji_chain": "\u5929\u673a\u4ee3\u535c\u94fe: \u65e0",
}
MUTATION_PHASES = (
    "greet_pending", "gift_bag_pending", "gift_pending", "dream_pending",
    "fragment_pending", "puzzle_pending", "reacquire_pending", "tianji_pending",
    "heart_pending", "heart_choice_pending", "heart_choice_reply_pending",
    "voyage_pending", "voyage_return_pending",
)


@pytest.fixture
def env(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_game_group_id(CHAT)
    state_module.set_game_bot_ids([BOT])
    state_module.set_identity_account(ID, 11)
    state_module.update_send_as_profile(ID, username="fixture_owner", sect_name="\u661f\u5bab")
    identity = state_module.get_identity_state(ID)
    identity.update(
        concubine_enabled=True,
        concubine_tianji_enabled=True,
        concubine_heart_enabled=True,
        concubine_phase="idle",
        concubine_availability="available",
        concubine_kind="\u9053\u5fc3\u4f8d\u59be",
        concubine_name=NAME,
        concubine_location="\u968f\u884c\u4e2d",
        concubine_affinity=320,
        concubine_oath="\u5b88\u79d8",
        concubine_tianji_chain="\u6b8b\u56fe\u5f15\u8def",
        concubine_tianji_chain_due_at=NOW + 1234,
        concubine_dream_due_at=NOW + 600,
        concubine_tianji_due_at=NOW + 700,
        concubine_heart_due_at=NOW + 800,
        concubine_last_snapshot_at=NOW - 3600,
        concubine_last_panel_msg_id=400,
        concubine_last_panel_chat_id=CHAT,
        concubine_last_error="dream_pending \u7b49\u5f85\u56de\u590d\u8d85\u65f6",
        concubine_tianji_last_error="tianji_pending \u7b49\u5f85\u56de\u590d\u8d85\u65f6",
        next_concubine_time=NOW + 100,
    )
    save, audit, send, gift = Mock(), AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(concubine, "save_state", save)
    monkeypatch.setattr(concubine, "mark_dirty", Mock())
    monkeypatch.setattr(concubine, "send_audit_log", audit)
    monkeypatch.setattr(concubine, "send_game_command", send)
    monkeypatch.setattr(concubine, "_send_gift_bag_command", gift)
    monkeypatch.setattr(concubine, "console_log", Mock())
    monkeypatch.setattr(concubine, "_record_concubine_ignored_reply", Mock())
    monkeypatch.setattr(concubine.random, "uniform", lambda *_args: 30)
    monkeypatch.setattr(passive_inbox, "save_state", save)
    monkeypatch.setattr(passive_inbox, "_save_passive_stats", Mock())
    monkeypatch.setattr(passive_inbox, "_observed_passive_events", {})
    monkeypatch.setattr(passive_inbox, "_passive_stats", {
        "total": 0, "changed": 0, "skipped": 0, "modules": {},
        "skip_reasons": {}, "recent": [],
    })
    with state_module.use_identity(ID):
        try:
            yield SimpleNamespace(identity=identity, save=save, audit=audit, send=send, gift=gift)
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(saved)


def apply(text=PANEL, at=NOW):
    parsed = concubine._parse_status_panel(text, at)
    return concubine._apply_status_snapshot(parsed, at)


def read_context(root=501, command_at=NOW - 1):
    return {"send_as_id": ID, "account_id": 11, "family": "concubine_status", "chat_id": CHAT,
            "root_msg_id": root, "reply_to_msg_id": root, "reply_to_sender_id": ID,
            "reply_to_command": concubine.CMD_CONCUBINE_STATUS, "reply_to_server_at": command_at,
            "reply_to_command_edited": False, "sender_id": BOT}


def legacy_pending(root=501):
    return {(CHAT, root): {"cmd": concubine.CMD_CONCUBINE_STATUS, "family": "concubine_status",
                          "chat_id": CHAT, "message_id": root, "account_id": 11, "sent_at": NOW - 1}}


def native(text, *, at=NOW, root=501, observed_at=None):
    return asyncio.run(concubine.handle_concubine_status_reply(
        text, at, SimpleNamespace(id=root, raw_text=concubine.CMD_CONCUBINE_STATUS, chat_id=CHAT, sender_id=ID),
        matched_family="concubine_status", current_msg_id=502, current_chat_id=CHAT,
        observed_at=at if observed_at is None else observed_at, reply_context=read_context(root),
    ))


def passive(text, at=NOW):
    return asyncio.run(passive_inbox.handle_passive_module_card(
        text, now=NOW, reply_context=read_context(), event_type="message",
        event=SimpleNamespace(id=502, chat_id=CHAT, sender_id=BOT, server_event_at=at),
    ))


def test_parser_does_not_fabricate_optional_observations():
    parsed = concubine._parse_status_panel(HEAD, NOW)
    assert not parsed or not (set(FIELDS) | set(OPTIONAL) | {"tianji_chain_due_at"}) & set(parsed)


@pytest.mark.parametrize("missing", tuple(FIELDS))
@pytest.mark.parametrize("route", ["snapshot", "miniapp", "native", "gift", "passive"])
def test_partial_panel_cannot_certify_freshness_or_release_a_chain(env, missing, route):
    text = "\n".join([HEAD, *(v for k, v in FIELDS.items() if k != missing)])
    if route in {"native", "gift"}:
        env.identity["concubine_phase"] = "gift_status_pending" if route == "gift" else "status_pending"
        env.identity["concubine_gift_status_msg_id" if route == "gift" else "concubine_status_msg_id"] = 501
        env.identity["concubine_affinity"] = 100
        env.identity["concubine_last_greet_day"] = concubine._local_day_key(NOW)
        env.identity["concubine_gift_attempt_day"] = concubine._local_day_key(NOW)
        env.identity["pending_tasks"] = legacy_pending()
    before = copy.deepcopy(env.identity)
    if route == "miniapp":
        result = concubine.sync_concubine_miniapp_status(text, NOW)
        assert not result["handled"]
    elif route in {"native", "gift"}:
        assert not native(text)
    elif route == "passive":
        assert not passive(text)
    else:
        assert not apply(text)
    assert env.identity == before
    env.gift.assert_not_awaited()
    env.save.assert_not_called()


@pytest.mark.parametrize("line", [
    "\u60c5\u7f18\u503c: -1", "\u60c5\u7f18\u503c: 184?",
    "\u60c5\u7f18\u503c: 184.5", "\u60c5\u7f18\u503c: 184/500",
    "\u60c5\u7f18\u503c: ", "\u60c5\u7f18\u503c: 184\n\u60c5\u7f18\u503c: 1000",
    "\u5929\u673a\u4ee3\u535c\u94fe: \u6b8b\u56fe\u5f15\u8def(\u5269\u4f59\u672a\u77e5)",
    "\u5929\u673a\u4ee3\u535c\u94fe: \u6b8b\u56fe\u5f15\u8def",
    "\u5f53\u524d\u8a93\u7ea6: ", "\u5f53\u524d\u8a93\u7ea6: \u65e0\n\u5f53\u524d\u8a93\u7ea6: \u5b88\u79d8",
])
def test_invalid_or_duplicate_fields_are_not_partially_applied(env, line):
    text = PANEL.replace(FIELDS["affinity"], line) if "\u60c5\u7f18\u503c" in line else PANEL + "\n" + line
    before = copy.deepcopy(env.identity)
    assert not apply(text)
    assert env.identity == before


@pytest.mark.parametrize("field", ["dream_due_at", "tianji_due_at", "heart_due_at"])
@pytest.mark.parametrize("value", [
    "", "\u672a\u77e5", "\u4e0d\u53ef\u7528", "\u4e0d\u53ef\u65bd\u5c55", "-5\u5206\u949f",
    "1.5\u5c0f\u65f6", "\u53ef\u7528\uff0c120\u5206\u949f", "2\u5c0f\u65f6 / 3\u5c0f\u65f6",
    "2\u5c0f\u65f6junk", "NaN", "Infinity",
])
def test_unknown_cooldown_is_not_ready(env, field, value):
    label = FIELDS[field].split(":")[0]
    before = copy.deepcopy(env.identity)
    assert not apply(PANEL.replace(FIELDS[field], f"{label}: {value}"))
    assert env.identity == before


@pytest.mark.parametrize("field", ["dream_due_at", "tianji_due_at", "heart_due_at"])
def test_conflicting_cooldown_lines_are_rejected(env, field):
    before = copy.deepcopy(env.identity)
    assert not apply(PANEL + "\n" + FIELDS[field].split(":")[0] + ": \u53ef\u65bd\u5c55")
    assert env.identity == before


def test_missing_optional_fields_preserve_same_partner_observations(env):
    before = copy.deepcopy(env.identity)
    assert apply()
    for key in ("concubine_oath", "concubine_tianji_chain", "concubine_tianji_chain_due_at"):
        assert env.identity[key] == before[key]
    assert env.identity["concubine_affinity"] == 184
    assert env.identity["concubine_last_snapshot_at"] == NOW


def test_explicit_zero_and_absence_are_authoritative_not_missing(env):
    text = PANEL.replace(FIELDS["affinity"], "\u60c5\u7f18\u503c: 0") + "\n" + "\n".join(OPTIONAL.values())
    assert apply(text)
    assert env.identity["concubine_affinity"] == 0
    assert env.identity["concubine_oath"] == "\u65e0"
    assert env.identity["concubine_tianji_chain"] == ""
    assert env.identity["concubine_tianji_chain_due_at"] == 0


def test_red_dust_panel_can_omit_affinity_without_erasing_existing_value(env):
    env.identity["concubine_kind"] = "\u7ea2\u5c18\u9053\u4fa3"
    text = PANEL.replace("\u9053\u5fc3\u4f8d\u59be", "\u7ea2\u5c18\u9053\u4fa3").replace("\n" + FIELDS["affinity"], "")
    assert apply(text)
    assert env.identity["concubine_affinity"] == 320


def test_new_partner_does_not_inherit_optional_partner_observations(env):
    text = PANEL.replace("\u9053\u5fc3\u4f8d\u59be", "\u7ea2\u5c18\u9053\u4fa3").replace(NAME, "\u82e5\u5170")
    assert apply(text.replace("\n" + FIELDS["affinity"], ""))
    assert env.identity["concubine_affinity"] == 0
    assert env.identity["concubine_oath"] == ""
    assert env.identity["concubine_tianji_chain"] == ""


@pytest.mark.parametrize("route", ["snapshot", "miniapp", "native", "passive"])
@pytest.mark.parametrize("at", [0, -1, float("nan"), float("inf"), NOW - 7200])
def test_invalid_or_older_observation_does_not_change_state(env, route, at):
    before = copy.deepcopy(env.identity)
    if route == "miniapp":
        assert not concubine.sync_concubine_miniapp_status(PANEL, at)["handled"]
    elif route == "native":
        assert not native(PANEL, at=at)
    elif route == "passive":
        assert not passive(PANEL, at)
    else:
        assert not apply(at=at)
    assert env.identity == before


@pytest.mark.parametrize("phase", MUTATION_PHASES)
@pytest.mark.parametrize("route", ["snapshot", "passive"])
def test_status_observation_does_not_clear_an_active_mutation(env, phase, route):
    env.identity["concubine_phase"] = phase
    env.identity["concubine_dream_msg_id"] = 601
    before = copy.deepcopy(env.identity)
    assert not (passive(PANEL) if route == "passive" else apply())
    assert env.identity == before


@pytest.mark.parametrize("pending", ["anchor", "task"])
def test_idle_phase_alone_is_not_permission_to_erase_pending_work(env, pending):
    if pending == "anchor":
        env.identity["concubine_dream_msg_id"] = 601
    else:
        env.identity["pending_tasks"] = {601: {"command": concubine.CMD_CONCUBINE_DREAM, "family": "concubine_dream"}}
    before = copy.deepcopy(env.identity)
    assert not passive(PANEL)
    assert env.identity == before


def test_miniapp_panel_cannot_make_an_old_telegram_anchor_fresh(env):
    assert concubine.sync_concubine_miniapp_status(PANEL, NOW)["handled"]
    assert env.identity["concubine_last_panel_msg_id"] == 0
    assert env.identity["concubine_last_panel_chat_id"] == 0
    assert concubine.heart_actions.panel_anchor(NOW) is None
    assert not concubine._has_recent_concubine_status_panel(NOW)


def test_legacy_status_completion_is_persisted_without_gift_followup(env):
    env.identity.update(
        concubine_phase="gift_status_pending",
        concubine_gift_status_msg_id=501,
        concubine_affinity=100,
        concubine_last_greet_day=concubine._local_day_key(NOW),
        concubine_gift_attempt_day=concubine._local_day_key(NOW),
        pending_tasks=legacy_pending(),
    )
    assert native(PANEL)
    env.save.assert_called_once()
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["concubine_gift_status_msg_id"] == 0
    assert not env.identity["pending_tasks"]
    env.gift.assert_not_awaited()


def test_passive_status_cannot_finish_an_unmatched_pending_query(env):
    env.identity.update(concubine_phase="status_pending", concubine_status_msg_id=601)
    before = copy.deepcopy(env.identity)
    assert not passive(PANEL)
    assert env.identity == before


def test_complete_panel_can_follow_an_incomplete_edit_for_the_same_query(env):
    env.identity.update(concubine_phase="status_pending", concubine_status_msg_id=501, pending_tasks=legacy_pending())
    assert not native(HEAD)
    assert env.identity["concubine_status_msg_id"] == 501
    assert native(PANEL)
    assert env.identity["concubine_phase"] == "idle"
    assert env.identity["concubine_status_msg_id"] == 0
    assert env.identity["concubine_affinity"] == 184


@pytest.mark.parametrize("text", [PANEL + "\n" + PANEL, PANEL + "\n\u4f60\u5c1a\u65e0\u4f8d\u59be"])
def test_contradictory_partner_panels_are_rejected(env, text):
    before = copy.deepcopy(env.identity)
    assert not apply(text)
    assert env.identity == before


@pytest.mark.parametrize("value", [
    "\u865a\u5929 4/0", "\u865a\u5929 5/4", "\u865a\u5929 -1/4",
    "\u865a\u5929 1/4 | \u865a\u5929 4/4", "\u865a\u5929 ?/4", "",
])
def test_declared_fragment_field_must_be_unambiguous(env, value):
    before = copy.deepcopy(env.identity)
    assert not apply(PANEL + "\n\u68a6\u56fe\u62fc\u7247: " + value)
    assert env.identity == before


@pytest.mark.parametrize("value", [
    "\u672a\u77e5", "\u5192\u9669\u822a\u7ebf\u8fdb\u884c\u4e2d\uff0c\u5269\u4f59\u7ea6\u672a\u77e5",
    "\u5192\u9669\u822a\u7ebf\u8fdb\u884c\u4e2d\uff0c\u5269\u4f59\u7ea6-5\u5206\u949f",
    "\u5192\u9669\u822a\u7ebf\u5df2\u5f52\u822a\uff0c\u5269\u4f59\u7ea630\u5206\u949f",
])
def test_declared_voyage_field_must_not_borrow_old_state(env, value):
    before = copy.deepcopy(env.identity)
    assert not apply(PANEL + "\n\u8fdc\u822a\u72b6\u6001: " + value)
    assert env.identity == before


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_valid_markdown_and_line_endings_are_accepted(env, newline):
    text = PANEL.replace(": ", ": **").replace("\n", "**\n") + "**"
    assert apply(text.replace("\n", newline))
    assert env.identity["concubine_affinity"] == 184


def test_valid_fragments_voyage_and_chain_are_preserved(env):
    text = PANEL + (
        "\n\u68a6\u56fe\u62fc\u7247: \u865a\u5929 1/4 | \u82cd\u5764 2/4"
        "\n\u5929\u673a\u4ee3\u535c\u94fe: \u6b8b\u56fe\u5f15\u8def(\u5269\u4f592\u5c0f\u65f6)"
        "\n\u8fdc\u822a\u72b6\u6001: \u5192\u9669\u822a\u7ebf\u8fdb\u884c\u4e2d\uff0c\u5269\u4f59\u7ea630\u5206\u949f\u3002"
    )
    assert apply(text)
    assert env.identity["concubine_fragment_xutian_count"] == 1
    assert env.identity["concubine_fragment_cangkun_count"] == 2
    assert env.identity["concubine_tianji_chain_due_at"] == NOW + 2 * 3600 + concubine.CD_BUFFER_SEC
    assert env.identity["concubine_voyage_return_at"] == NOW + 30 * 60 + concubine.CD_BUFFER_SEC


def test_status_snapshot_keeps_the_observation_clock_not_the_apply_clock(env):
    parsed = concubine._parse_status_panel(PANEL, NOW)
    assert concubine._apply_status_snapshot(parsed, NOW + 600)
    assert env.identity["concubine_last_snapshot_at"] == NOW


def test_no_partner_observation_does_not_erase_the_chronology_guard(env):
    assert apply("\u4f60\u5c1a\u65e0\u4f8d\u59be", NOW)
    before = copy.deepcopy(env.identity)
    assert env.identity["concubine_last_snapshot_at"] == NOW
    assert not apply(PANEL, NOW - 1)
    assert env.identity == before


@pytest.mark.parametrize("source_time", [0, float("nan"), NOW - 7200, NOW + 7200])
def test_native_panel_must_use_valid_server_time(env, source_time):
    before = copy.deepcopy(env.identity)
    assert not native(PANEL, observed_at=source_time)
    assert env.identity == before


def test_native_delayed_panel_anchors_cooldown_to_source_not_delivery(env):
    assert native(PANEL, at=NOW + 3600, observed_at=NOW)
    assert env.identity["concubine_last_snapshot_at"] == NOW
    assert env.identity["concubine_dream_due_at"] == NOW + 7200 + 65
    assert env.identity["concubine_last_panel_chat_id"] == CHAT


@pytest.mark.parametrize("routed", [False, True])
def test_passive_panel_uses_server_clock_and_does_not_repeat_routed_completion(env, routed):
    if routed:
        assert native(PANEL)
    before = copy.deepcopy(env.identity)
    assert not asyncio.run(passive_inbox.handle_passive_module_card(
        PANEL, now=NOW,
        reply_context=dict(read_context(command_at=NOW - 1 if routed else NOW - 7201), routed_reply_handled=routed),
        event=SimpleNamespace(id=502, chat_id=CHAT, sender_id=BOT, server_event_at=NOW if routed else NOW - 7200),
        event_type="message",
    ))
    assert env.identity == before


def test_passive_current_panel_retains_its_own_anchor_and_clock(env):
    assert asyncio.run(passive_inbox.handle_passive_module_card(
        PANEL, now=NOW + 300,
        reply_context=read_context(),
        event=SimpleNamespace(id=502, chat_id=CHAT, sender_id=BOT, server_event_at=NOW),
        event_type="edit",
    ))
    assert env.identity["concubine_last_snapshot_at"] == NOW
    assert env.identity["concubine_last_panel_msg_id"] == 502
    assert env.identity["concubine_last_panel_chat_id"] == CHAT


@pytest.mark.parametrize("lookup", [concubine.heart_actions.panel_anchor, concubine._has_recent_concubine_status_panel])
def test_future_cached_panels_are_not_reusable(env, lookup):
    env.identity["concubine_last_snapshot_at"] = NOW + 100
    assert not lookup(NOW)


def test_incomplete_logged_panel_is_not_a_heart_anchor(env, monkeypatch):
    monkeypatch.setattr(concubine, "_iter_message_log_entries_between", lambda *_args: [{
        "event_type": "message", "message_id": 502, "chat_id": CHAT,
        "text": HEAD + "\n\u5171\u5386\u5fc3\u52ab\u51b7\u5374: \u53ef\u65bd\u5c55",
        "ts": "fixture",
    }])
    monkeypatch.setattr(concubine, "_parse_message_log_ts", lambda _value: NOW - 20)
    assert concubine.heart_actions.panel_anchor(NOW) is None


def test_rejected_markdown_status_cannot_fall_through_to_fragment_reward(env):
    text = PANEL.replace(HEAD, HEAD.replace(": ", ": " + chr(96)).replace(" (", chr(96) + " ("))
    text = text.replace(FIELDS["dream_due_at"], "\u5165\u68a6\u5bfb\u56fe\u51b7\u5374: \u672a\u77e5")
    text += "\n\u6b8b\u56fe\u8fdb\u5ea6\u5df2\u81f3 4/4"
    before = copy.deepcopy(env.identity)
    assert not passive(text)
    assert env.identity == before


@pytest.mark.parametrize("bad_clock", [None, "bad", float("nan"), float("inf"), -1, 0, 1e100, True])
def test_gift_status_checks_time_before_formatting_the_day(env, bad_clock):
    env.identity.update(concubine_phase="gift_status_pending", concubine_gift_status_msg_id=501)
    before = copy.deepcopy(env.identity)
    assert not native(PANEL, at=bad_clock)
    assert env.identity == before


@pytest.mark.parametrize("lookup", [concubine.heart_actions.panel_anchor, concubine._has_recent_concubine_status_panel])
@pytest.mark.parametrize("value", ["bad", float("inf"), float("nan"), 1e100])
def test_corrupt_panel_clock_is_not_reusable(env, lookup, value):
    env.identity["concubine_last_snapshot_at"] = value
    assert not lookup(NOW)


@pytest.mark.parametrize("line", [
    "\u60c5\u7f18\u503c: 99999999999999999999",
    "\u68a6\u56fe\u62fc\u7247: \u865a\u5929 1/99999999999999999999",
    "\u68a6\u56fe\u62fc\u7247: \u865a\u5929 1/" + "9" * 5000,
    "\u5929\u673a\u4ee3\u535c\u51b7\u5374: 9999999999999999999999999\u5c0f\u65f6",
])
def test_status_numbers_must_fit_persistence_and_calendar_bounds(env, line):
    text = PANEL
    for old in FIELDS.values():
        if old.split(":")[0] == line.split(":")[0]:
            text = text.replace(old, line)
            break
    else:
        text += "\n" + line
    before = copy.deepcopy(env.identity)
    assert not apply(text)
    assert env.identity == before
