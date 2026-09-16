import copy
import asyncio
import json
import random
from datetime import datetime
from unittest.mock import AsyncMock

import pytest

from model import state as state_module
from model.features import wanxin
from model.wanxin_commission_replay import find_commission_evidence
from test_cultivation_accounting import ACCOUNT, IDENTITY, env  # noqa: F401


CHAT = -10065001
BOT = 65099
HELPER = IDENTITY + 1
OUTSIDER = 80022
BASE = 1_800_000_000
ACCEPT = "【咒契协定已成】\n阴罗宗弟子 @outside_one 已接取 @owner_one 的解咒委托。"
STRIP = "【剥离咒源成功】\n@outside_one 替 @owner_one 剥下一段阴罗残咒。\n魂封 -8，咒源 +14。\n婉心：120\n魂封：0\n月魄：52\n咒源：120"


def row(msg_id, text, sender, at, *, reply=0, chat=CHAT, kind="message"):
    return {
        "event_type": kind, "chat_id": chat, "message_id": msg_id, "sender_id": sender,
        "server_event_at": at, "text": text, "reply_to_msg_id": reply,
        "ts": datetime.fromtimestamp(at, wanxin.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8"),
    }


def logs(*, completed=False):
    entries = [
        row(100, ".发布解咒委托 1", IDENTITY, BASE),
        row(110, "【解咒委托已发布】\n委托 ID：23", BOT, BASE + 2, reply=100),
        row(120, ".接取解咒委托 23", OUTSIDER, BASE + 10),
        row(130, ACCEPT, BOT, BASE + 12, reply=120),
    ]
    if completed:
        entries += [row(140, ".剥离咒源 @owner_one", OUTSIDER, BASE + 20),
                    row(150, STRIP, BOT, BASE + 22, reply=140)]
    return entries


@pytest.fixture
def owner(env, monkeypatch):  # noqa: F811
    state_module.set_game_group_id(CHAT)
    state_module.set_game_bot_ids([BOT])
    state_module.update_send_as_profile(IDENTITY, username="owner_one", enabled=True)
    state_module.set_identity_account(HELPER, ACCOUNT + 1)
    state_module.update_send_as_profile(HELPER, username="helper_one", sect_name="阴罗宗", enabled=True)
    observed = wanxin.normalize_wanxin_observation({
        "commission": {"id": 23, "published_at": BASE + 2, "publish_msg_id": 100, "owner_username": "owner_one"},
        "assist": {"send_as_id": HELPER},
    })
    env["wanxin_enabled"] = True
    env["wanxin_observation"] = copy.deepcopy(observed)
    with state_module.use_identity(IDENTITY):
        yield observed


def provide(monkeypatch, entries):
    monkeypatch.setattr(wanxin, "_iter_message_log_entries_between",
                        lambda *_args: [(entry, BASE + 30) for entry in entries])


def find(entries, commission, *, chats=(CHAT,), aliases=None):
    return find_commission_evidence(
        entries, owner_id=IDENTITY, helper_id=HELPER, commission=commission,
        identity_usernames=aliases or {IDENTITY: {"owner_one"}, HELPER: {"helper_one"}},
        game_chats=chats, game_bots=[BOT], now=BASE + 40, parse_reply=wanxin.parse_wanxin_text,
    )


def test_native_external_claim_preserves_publication_based_cancel_deadline(owner, monkeypatch):
    provide(monkeypatch, logs())
    assert wanxin._recover_external_commission_claim_from_log(owner, BASE + 30)
    assert owner["commission"]["claimed_elsewhere"]
    assert owner["commission"]["claim_helper_username"] == "outside_one"
    assert owner["commission"]["cancel_due_at"] == BASE + 2 + 86400 + wanxin.CD_BUFFER_SEC


@pytest.mark.parametrize("damage", ["sender", "chat", "clock", "publication", "accept_id", "edited_outcome"])
def test_external_claim_rejects_unowned_or_unqualified_evidence(owner, monkeypatch, damage):
    entries = logs()
    if damage == "sender":
        entries[-1].update(sender_id=BOT + 1, sender_is_bot=True, sender_username="copied_bot")
    elif damage == "chat":
        entries[-1]["chat_id"] = CHAT - 1
    elif damage == "clock":
        entries[-1].pop("server_event_at")
    elif damage == "publication":
        entries.pop(0)
    elif damage == "accept_id":
        entries[-2]["text"] = ".接取解咒委托 24"
    else:
        entries.append(row(130, "", BOT, BASE + 25, reply=120, kind="edit"))
    before = copy.deepcopy(owner)
    provide(monkeypatch, entries)
    assert not wanxin._recover_external_commission_claim_from_log(owner, BASE + 30)
    assert owner == before


def test_native_external_completion_consumes_only_its_proved_commission(owner, monkeypatch):
    owner["commission"].update(claimed_elsewhere=True, claim_helper_username="outside_one")
    provide(monkeypatch, logs(completed=True))
    assert wanxin._recover_claimed_commission_completion_from_log(owner, BASE + 30)
    assert owner["commission"]["id"] == 0
    assert owner["moon_soul"] == 52


@pytest.mark.parametrize("damage", ["sender", "helper", "clock", "accept", "strip_command", "replacement", "edited_outcome"])
def test_external_completion_cannot_consume_from_an_unrelated_result(owner, monkeypatch, damage):
    owner["commission"].update(claimed_elsewhere=True, claim_helper_username="outside_one")
    entries = logs(completed=True)
    if damage == "sender":
        entries[-1]["sender_id"] = OUTSIDER
    elif damage == "helper":
        entries[-2]["sender_id"] = OUTSIDER + 1
    elif damage == "clock":
        entries[-1]["server_event_at"] = BASE - 100
    elif damage == "accept":
        entries[2]["text"] = ".接取解咒委托 24"
    elif damage == "strip_command":
        entries[-2]["text"] = ".剥离咒源 @someone_else"
    elif damage == "replacement":
        entries[4:4] = [row(132, ".发布解咒委托 1", IDENTITY, BASE + 14),
                        row(134, "【解咒委托已发布】\n委托 ID：24", BOT, BASE + 16, reply=132)]
    else:
        entries.append(row(150, "", BOT, BASE + 25, reply=140, kind="edit"))
    before = copy.deepcopy(owner)
    provide(monkeypatch, entries)
    assert not wanxin._recover_claimed_commission_completion_from_log(owner, BASE + 30)
    assert owner == before


@pytest.mark.parametrize("seed", range(5))
def test_duplicates_and_delivery_order_do_not_change_a_native_chain(owner, seed):
    entries = logs(completed=True)
    entries += copy.deepcopy(entries)
    random.Random(seed).shuffle(entries)
    evidence = find(entries, owner["commission"])
    assert evidence is not None and evidence.completion is not None
    assert evidence.completion.end["at"] == BASE + 22


@pytest.mark.parametrize("command,text", [
    (".发布解咒委托 1", "你已有进行中的解咒委托（ID: 23），不可重复发布。"),
    (".取消解咒委托", "委托已被接取，无法直接取消。"),
])
def test_explicit_unchanged_commission_replies_do_not_hide_completion(owner, command, text):
    entries = logs(completed=True)
    entries += [row(132, command, IDENTITY, BASE + 14), row(134, text, BOT, BASE + 16, reply=132)]
    evidence = find(entries, owner["commission"])
    assert evidence is not None and evidence.completion is not None


@pytest.mark.parametrize("kind", ["cancelled", "different_existing", "unknown"])
def test_owner_lifecycle_changes_or_unknown_results_reject_completion(owner, kind):
    entries = logs(completed=True)
    command, text = {
        "cancelled": (".取消解咒委托", "解咒委托已取消，已退回 1 灵石。"),
        "different_existing": (".发布解咒委托 1", "你已有进行中的解咒委托（ID: 24），不可重复发布。"),
        "unknown": (".发布解咒委托 1", "未知结果"),
    }[kind]
    entries += [row(132, command, IDENTITY, BASE + 14), row(134, text, BOT, BASE + 16, reply=132)]
    evidence = find(entries, owner["commission"])
    assert evidence is None or evidence.completion is None


def test_intermediate_official_reply_is_a_valid_link(owner):
    entries = logs(completed=True)
    entries[-1]["reply_to_msg_id"] = 145
    entries.append(row(145, "正在剥离咒源", BOT, BASE + 21, reply=140))
    assert find(entries, owner["commission"]).completion is not None


@pytest.mark.parametrize("damage", ["sender_edit", "reply_edit", "duplicate_sender_type", "future_clock"])
def test_conflicting_intermediate_link_cannot_authorize_completion(owner, damage):
    entries = logs(completed=True)
    entries[-1]["reply_to_msg_id"] = 145
    intermediate = row(145, "正在剥离咒源", BOT, BASE + 21, reply=140)
    entries.append(intermediate)
    if damage == "future_clock":
        intermediate["server_event_at"] = BASE + 30
    elif damage == "duplicate_sender_type":
        entries.append({**intermediate, "sender_id": float(BOT)})
    else:
        revised = {**intermediate, "event_type": "edit", "server_event_at": BASE + 23}
        revised["sender_id" if damage == "sender_edit" else "reply_to_msg_id"] = OUTSIDER
        entries.append(revised)
    evidence = find(entries, owner["commission"])
    assert evidence is None or evidence.completion is None


@pytest.mark.parametrize("reverse", [False, True])
def test_incomparable_same_second_replies_do_not_pick_a_success_by_arrival_order(owner, reverse):
    entries = logs(completed=True)
    entries[-1]["event_type"] = "edit"
    entries.append(row(151, "结果待定", BOT, BASE + 22, reply=140, kind="edit"))
    if reverse:
        entries.reverse()
    evidence = find(entries, owner["commission"])
    assert evidence is None or evidence.completion is None


@pytest.mark.parametrize("reverse", [False, True])
def test_latest_unknown_sibling_reply_suppresses_old_terminal_text(owner, reverse):
    entries = logs(completed=True)
    entries.append(row(151, "结果待定", BOT, BASE + 25, reply=140))
    if reverse:
        entries.reverse()
    evidence = find(entries, owner["commission"])
    assert evidence is None or evidence.completion is None


def test_distinct_native_times_allow_cross_chat_commission_chain(owner):
    entries = logs(completed=True)
    for item in entries[2:]:
        item["chat_id"] = CHAT - 1
    evidence = find(entries, owner["commission"], chats=(CHAT, CHAT - 1))
    assert evidence.completion is not None


def test_same_second_cross_chat_lifecycle_is_not_ordered_by_message_id(owner):
    entries = logs(completed=True)
    for item in entries[2:]:
        item["chat_id"] = CHAT - 1
    entries[2]["server_event_at"] = entries[1]["server_event_at"]
    assert find(entries, owner["commission"], chats=(CHAT, CHAT - 1)) is None


def test_channel_command_actors_and_renamed_targets_keep_native_ownership(owner):
    entries = logs(completed=True)
    owner_id, external_id = 3_580_000_001, 3_580_000_002
    entries[0]["sender_id"] = -1_000_000_000_000 - owner_id
    for index in (2, 4):
        entries[index]["sender_id"] = -1_000_000_000_000 - external_id
    entries[-1]["text"] = STRIP.replace("outside_one", "outside_new").replace("owner_one", "owner_new")
    entries[-2]["text"] = ".剥离咒源 @owner_new"
    evidence = find_commission_evidence(
        entries, owner_id=owner_id, helper_id=HELPER, commission=owner["commission"],
        identity_usernames={owner_id: {"owner_one", "owner_new"}}, game_chats=[CHAT], game_bots=[BOT],
        now=BASE + 40, parse_reply=wanxin.parse_wanxin_text,
    )
    assert evidence.completion is not None


def test_shared_alias_does_not_select_an_owner(owner):
    assert find(logs(completed=True), owner["commission"], aliases={
        IDENTITY: {"owner_one"}, IDENTITY + 100: {"owner_one"},
    }) is None


@pytest.mark.parametrize("recover", ["claim", "completion"])
def test_external_recovery_does_not_clear_an_unrelated_pending_action(owner, monkeypatch, recover):
    owner["pending"] = {"action": "moon_greet", "msg_id": 201}
    owner["commission"]["claimed_elsewhere"] = recover == "completion"
    before = copy.deepcopy(owner)
    provide(monkeypatch, logs(completed=True))
    fn = wanxin._recover_external_commission_claim_from_log if recover == "claim" else wanxin._recover_claimed_commission_completion_from_log
    assert not fn(owner, BASE + 30)
    assert owner == before


@pytest.mark.parametrize("panel_time", [BASE + 22, BASE + 25])
def test_old_or_same_clock_completion_does_not_overwrite_a_current_panel(owner, monkeypatch, panel_time):
    owner.update(panel_observed_at=panel_time, moon_soul=80)
    owner["commission"]["claimed_elsewhere"] = True
    provide(monkeypatch, logs(completed=True))
    assert wanxin._recover_claimed_commission_completion_from_log(owner, BASE + 30)
    assert owner["commission"]["id"] == 0
    assert owner["moon_soul"] == 80
    assert owner["panel_observed_at"] == panel_time


def test_native_publication_corrects_a_longer_unverified_cancel_deadline(owner, monkeypatch):
    owner["commission"]["cancel_due_at"] = BASE + 10 * 86400
    provide(monkeypatch, logs())
    assert wanxin._recover_external_commission_claim_from_log(owner, BASE + 30)
    assert owner["commission"]["cancel_due_at"] == BASE + 2 + 86400 + wanxin.CD_BUFFER_SEC


def test_cosmetic_publication_edit_does_not_restart_the_24_hour_clock(owner, monkeypatch):
    entries = logs() + [
        row(110, "【解咒委托已发布】\n委托 ID：23\n等待咒师接取。", BOT, BASE + 25, reply=100, kind="edit"),
    ]
    provide(monkeypatch, entries)
    assert wanxin._recover_external_commission_claim_from_log(owner, BASE + 30)
    assert owner["commission"]["published_at"] == BASE + 2
    assert owner["commission"]["cancel_due_at"] == BASE + 2 + 86400 + wanxin.CD_BUFFER_SEC
    provide(monkeypatch, [*entries, *logs(completed=True)[4:]])
    assert wanxin._recover_claimed_commission_completion_from_log(owner, BASE + 30)


def test_scheduler_consumes_already_completed_external_chain_on_the_first_pass(owner, monkeypatch):
    provide(monkeypatch, logs(completed=True))
    send = AsyncMock()
    monkeypatch.setattr(wanxin, "send_game_command", send)
    monkeypatch.setattr(wanxin, "save_state", lambda: None)
    state_module.state["wanxin_observation"] = copy.deepcopy(owner)
    asyncio.run(wanxin.run_wanxin_scheduler(BASE + 30))
    observed = state_module.state["wanxin_observation"]
    send.assert_not_awaited()
    assert observed["commission"]["id"] == 0
    assert not observed["commission"]["claimed_elsewhere"]
    assert observed["moon_soul"] == 52
    assert observed["auto_next_time"] == BASE + 30 + wanxin.WANXIN_CHAIN_STEP_SEC


def test_first_confirmed_publication_edit_sets_the_native_deadline(owner, monkeypatch):
    entries = logs()
    entries[1]["text"] = "正在发布解咒委托"
    entries += [
        row(110, "【解咒委托已发布】\n委托 ID：23", BOT, BASE + 4, reply=100, kind="edit"),
        row(110, "【解咒委托已发布】\n委托 ID：23\n等待咒师接取。", BOT, BASE + 6, reply=100, kind="edit"),
    ]
    provide(monkeypatch, entries)
    assert wanxin._recover_external_commission_claim_from_log(owner, BASE + 30)
    assert owner["commission"]["published_at"] == BASE + 4


def test_publication_correction_cannot_reuse_a_superseded_early_clock(owner):
    entries = logs(completed=True) + [
        row(110, "【解咒委托已发布】\n委托 ID：24", BOT, BASE + 16, reply=100, kind="edit"),
        row(110, "【解咒委托已发布】\n委托 ID：23", BOT, BASE + 25, reply=100, kind="edit"),
    ]
    assert find(entries, owner["commission"]) is None


def test_renamed_owner_on_a_cosmetic_accept_edit_keeps_native_acceptance_time(owner):
    entries = logs(completed=True) + [
        row(130, ACCEPT.replace("owner_one", "owner_new"), BOT, BASE + 25, reply=120, kind="edit"),
    ]
    evidence = find(entries, owner["commission"], aliases={IDENTITY: {"owner_one", "owner_new"}})
    assert evidence.completion is not None
    assert evidence.acceptance.end["at"] == BASE + 12
    assert evidence.acceptance.revision["at"] == BASE + 25


def test_foreign_literal_owner_name_does_not_reuse_another_actors_early_acceptance(owner):
    entries = logs(completed=True)
    entries[3]["text"] = ACCEPT.replace("@owner_one", "@owner")
    entries.append(row(130, ACCEPT, BOT, BASE + 25, reply=120, kind="edit"))
    evidence = find(entries, owner["commission"])
    assert evidence is not None
    assert evidence.acceptance.end["at"] == BASE + 25
    assert evidence.completion is None


def test_completion_panel_uses_edit_clock_while_lifecycle_keeps_first_confirmation(owner, monkeypatch):
    entries = logs(completed=True) + [
        row(150, STRIP.replace("月魄：52", "月魄：80"), BOT, BASE + 25, reply=140, kind="edit"),
    ]
    evidence = find(entries, owner["commission"])
    assert evidence.completion.end["at"] == BASE + 22
    assert evidence.completion.revision["at"] == BASE + 25
    provide(monkeypatch, entries)
    owner.update(panel_observed_at=BASE + 23, moon_soul=60)
    owner["commission"]["claimed_elsewhere"] = True
    assert wanxin._recover_claimed_commission_completion_from_log(owner, BASE + 30)
    assert owner["moon_soul"] == 80
    assert owner["panel_observed_at"] == BASE + 25


def test_existing_jsonl_reader_preserves_native_chain_and_ignores_non_objects(owner, monkeypatch, tmp_path):
    monkeypatch.setattr(wanxin, "MESSAGES_DIR", tmp_path)
    path = tmp_path / f"{datetime.fromtimestamp(BASE, wanxin.TZ_LOCAL).date().isoformat()}.log"
    path.write_text("[]\nnull\ninvalid\n" + "\n".join(json.dumps(item) for item in logs(completed=True)) + "\n")
    owner["commission"]["claimed_elsewhere"] = True
    assert wanxin._recover_claimed_commission_completion_from_log(owner, BASE + 30)
    assert owner["commission"]["id"] == 0


@pytest.mark.parametrize("command", [".发布解咒委托 1", ".取消解咒委托"])
def test_intervening_owner_command_without_a_reply_keeps_outcome_unknown(owner, command):
    entries = logs(completed=True) + [row(132, command, IDENTITY, BASE + 14)]
    evidence = find(entries, owner["commission"])
    assert evidence is None or evidence.completion is None


def test_newer_publication_prevents_restoring_an_old_commission(owner):
    entries = logs(completed=True) + [
        row(160, ".发布解咒委托 1", IDENTITY, BASE + 25),
        row(170, "【解咒委托已发布】\n委托 ID：24", BOT, BASE + 27, reply=160),
    ]
    assert find(entries, owner["commission"]) is None


@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_owner_command_does_not_disappear_from_the_history(owner, reverse):
    command = row(132, ".发布解咒委托 1", IDENTITY, BASE + 14)
    entries = logs(completed=True) + [command, {**command, "sender_id": float(IDENTITY)}]
    if reverse:
        entries.reverse()
    assert find(entries, owner["commission"]) is None


def test_two_native_acceptances_cannot_choose_only_the_external_actor(owner):
    entries = logs(completed=True) + [
        row(122, ".接取解咒委托 23", HELPER, BASE + 11),
        row(132, ACCEPT.replace("outside_one", "helper_one"), BOT, BASE + 13, reply=122),
    ]
    assert find(entries, owner["commission"]) is None


@pytest.mark.parametrize("reverse", [False, True])
def test_corrupt_later_sibling_cannot_reveal_an_earlier_success(owner, reverse):
    entries = logs(completed=True) + [
        row(151, "结果待定", BOT, BASE + 25, reply=140),
        row(151, "结果待定", OUTSIDER, BASE + 26, reply=140, kind="edit"),
    ]
    if reverse:
        entries.reverse()
    evidence = find(entries, owner["commission"])
    assert evidence is None or evidence.completion is None


def test_terminal_edit_of_intermediate_text_completes_once(owner, monkeypatch):
    entries = logs(completed=True)
    entries[-1]["text"] = "正在剥离咒源"
    entries.append(row(150, STRIP, BOT, BASE + 25, reply=140, kind="edit"))
    owner["commission"]["claimed_elsewhere"] = True
    provide(monkeypatch, entries)
    assert wanxin._recover_claimed_commission_completion_from_log(owner, BASE + 30)
    before = copy.deepcopy(owner)
    assert not wanxin._recover_claimed_commission_completion_from_log(owner, BASE + 31)
    assert owner == before


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), "1800000022", -1])
def test_invalid_native_clock_never_becomes_a_result(owner, value):
    entries = logs(completed=True)
    entries[-1]["server_event_at"] = value
    evidence = find(entries, owner["commission"])
    assert evidence is None or evidence.completion is None


@pytest.mark.parametrize("field,value", [
    ("event_type", []), ("event_type", {}), ("chat_id", []), ("message_id", "150"),
    ("sender_id", {"id": BOT}), ("reply_to_msg_id", [140]), ("text", [STRIP]),
    ("message_id", 2 ** 64), ("sender_id", -(2 ** 64)),
])
def test_malformed_metadata_is_not_a_recoverable_result(owner, field, value):
    entries = logs(completed=True)
    entries[-1][field] = value
    evidence = find(entries, owner["commission"])
    assert evidence is None or evidence.completion is None


@pytest.mark.parametrize("value", [True, "100", 100.0, -1])
def test_malformed_publication_anchor_never_gains_authority(owner, value):
    owner["commission"]["publish_msg_id"] = value
    assert find(logs(completed=True), owner["commission"]) is None


def test_record_overflow_does_not_yield_a_partial_history(owner, monkeypatch, tmp_path):
    monkeypatch.setattr(wanxin, "MESSAGES_DIR", tmp_path)
    monkeypatch.setattr(wanxin, "WANXIN_LOG_MAX_RECORDS", 5)
    path = tmp_path / f"{datetime.fromtimestamp(BASE, wanxin.TZ_LOCAL).date().isoformat()}.log"
    path.write_text("\n".join(json.dumps(item) for item in logs(completed=True)) + "\n")
    assert list(wanxin._iter_message_log_entries_between(BASE, BASE + 30)) == []


def test_history_reads_have_a_time_and_per_file_byte_bound(monkeypatch, tmp_path):
    monkeypatch.setattr(wanxin, "MESSAGES_DIR", tmp_path)
    calls = []

    def tail(path, *, max_bytes):
        calls.append((path, max_bytes))
        return []

    monkeypatch.setattr(wanxin, "_read_log_tail_lines", tail)
    assert list(wanxin._iter_message_log_entries_between(BASE - 100 * 86400, BASE)) == []
    assert 1 <= len(calls) <= 3
    assert all(path.parent == tmp_path and size == 1024 * 1024 for path, size in calls)


def test_truncated_publication_is_not_replaced_by_a_sent_record(owner, monkeypatch, tmp_path):
    monkeypatch.setattr(wanxin, "MESSAGES_DIR", tmp_path)
    path = tmp_path / f"{datetime.fromtimestamp(BASE, wanxin.TZ_LOCAL).date().isoformat()}.log"
    entries = logs(completed=True)
    prefix = "\n".join(json.dumps(item) for item in entries[:2])
    padding = "x" * 1000 + "\n"
    suffix = "\n".join(json.dumps(item) for item in [
        *entries[2:], {**entries[0], "event_type": "sent"}, entries[1],
    ])
    path.write_text(prefix + "\n" + padding * 1100 + suffix + "\n")
    owner["commission"]["claimed_elsewhere"] = True
    before = copy.deepcopy(owner)
    assert not wanxin._recover_claimed_commission_completion_from_log(owner, BASE + 30)
    assert owner == before


@pytest.mark.parametrize("start,end", [(BASE, 1e100), (1e100, 1e100), (True, BASE), (BASE, float("inf"))])
def test_invalid_log_window_is_empty(start, end):
    assert list(wanxin._iter_message_log_entries_between(start, end)) == []
