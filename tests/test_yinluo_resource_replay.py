import copy
import itertools

import pytest

from model import yinluo_resource_replay as replay
from model.yinluo_resource_facts import parse_yinluo_resource_reply


CHAT = -10011
NOW = 1_800_000_000.0


def row(msg_id, text, *, sender=99, parent=0, at=NOW - 5, event_type="message", chat=CHAT):
    return {
        "chat_id": chat, "message_id": msg_id, "sender_id": sender,
        "reply_to_msg_id": parent, "text": text, "server_event_at": at, "event_type": event_type,
    }


def original(**changes):
    return {**row(100, ".召唤魔影", sender=1001, at=NOW - 20), **changes}


def decode(entries, **changes):
    args = {"identity_accounts": {1001: 11}, "game_chats": {CHAT, CHAT - 1}, "game_bots": {99}, "now": NOW}
    return replay.owned_yinluo_log_events(entries, **{**args, **changes})


def test_chain_preserves_original_actor_and_charge_plus_backlash():
    entries = [
        original(),
        row(105, "你消耗了 5000 点修为，召唤魔域的投影。", parent=100, at=NOW - 10),
        row(110, "召唤成功，镇压失败！修为暴跌了 1362 点！", parent=105),
    ]
    before = copy.deepcopy(entries)
    decoded = decode(entries)
    assert entries == before
    assert len(decoded) == 2
    assert {source.command_msg_id for _, source in decoded} == {100}
    assert {source.identity_id for _, source in decoded} == {1001}
    facts = [parse_yinluo_resource_reply(source.command.text, event.text) for event, source in decoded]
    assert [item.effects[0].amount for item in facts] == [-5000, -1362]
    assert decoded[-1][0].reply_context["resource_direct_reply_id"] == 105


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_native_batch_input_order_is_not_the_command_clock(order):
    entries = [original(), row(105, "", parent=100, at=NOW - 10), row(110, "结果", parent=105)]
    decoded = decode([entries[index] for index in order])
    assert len(decoded) == 2
    assert all(source.command_at == NOW - 20 for _, source in decoded)


def test_empty_final_edit_is_preserved_for_withdrawal_not_filtered_as_unknown_text():
    entries = [
        original(), row(110, "召唤成功，镇压失败！修为暴跌了 1362 点！", parent=100, at=NOW - 10),
        row(110, "", parent=100, event_type="edit", at=NOW),
    ]
    decoded = decode(entries)
    assert len(decoded) == 2
    assert decoded[-1][0].text == ""
    assert decoded[-1][1].edited
    assert decoded[-1][1].result_at == NOW


@pytest.mark.parametrize("changes", [
    {"event_type": "sent"}, {"event_type": "edit"}, {"sender_id": 99}, {"sender_id": 2002},
    {"server_event_at": 0}, {"server_event_at": str(NOW - 20)}, {"server_event_at": True},
    {"server_event_at": NOW}, {"message_id": True}, {"chat_id": CHAT - 1}, {"text": ".深度闭关"},
])
def test_missing_or_unowned_original_is_not_rebuilt_from_local_metadata(changes):
    command = original(**changes, ts_epoch=NOW - 20, sent_at=NOW - 20, send_as_id=1001, family="yinluo_demon_summon")
    assert decode([command, row(110, "结果", parent=100)]) == []


@pytest.mark.parametrize("changes", [
    {"sender_id": 123}, {"sender_id": True}, {"chat_id": CHAT - 1},
    {"reply_to_msg_id": 105}, {"reply_to_msg_id": True}, {"reply_to_msg_id": 110},
    {"server_event_at": NOW + 1}, {"server_event_at": 0},
])
def test_untrusted_or_cyclic_intermediate_node_cannot_bridge_a_reply(changes):
    middle = {**row(105, "中间消息", parent=100, at=NOW - 10), **changes}
    result = row(110, "结果", parent=105)
    assert all(event.msg_id != 110 for event, _source in decode([original(), middle, result]))


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_conflicting_original_does_not_recover_by_another_identical_copy(order):
    entries = [original(), original(text=".化功为煞 1000"), original()]
    assert decode([*(entries[index] for index in order), row(110, "结果", parent=100)]) == []


def test_equal_message_ids_in_two_chats_retain_independent_command_ownership():
    entries = [original(), row(110, "结果", parent=100)]
    entries += [original(chat_id=CHAT - 1), row(110, "结果", parent=100, chat=CHAT - 1)]
    decoded = decode(entries)
    assert len(decoded) == 2
    assert len({source.component_key("summon_cost") for _, source in decoded}) == 2


def test_command_in_a_different_chat_cannot_fill_a_missing_original():
    assert decode([original(chat_id=CHAT - 1), row(110, "结果", parent=100)]) == []


def test_edited_command_is_not_used_in_place_of_a_missing_original():
    assert decode([original(event_type="edit"), row(110, "结果", parent=100)]) == []


def test_known_original_is_not_replaced_by_later_edited_command_text():
    decoded = decode([original(), original(text=".化功为煞 1000", event_type="edit"), row(110, "结果", parent=100)])
    assert len(decoded) == 1
    assert decoded[0][1].command.text == ".召唤魔影"


def test_channel_sender_must_resolve_to_exactly_one_managed_identity():
    entries = [original(sender_id=-1001001), row(110, "结果", parent=100)]
    assert decode(entries)[0][1].identity_id == 1001
    assert decode(entries, identity_accounts={1001: 11, 1001001: 11}) == []


def test_reply_traversal_has_a_fixed_depth_bound():
    entries = [original()]
    for index in range(1, replay.MAX_REPLY_DEPTH + 2):
        entries.append(row(100 + index, "", parent=99 + index, at=NOW - 20 + index))
    decoded = decode(entries)
    assert {event.msg_id for event, _source in decoded} == set(range(101, 101 + replay.MAX_REPLY_DEPTH))


def test_oversized_input_is_an_explicit_error_not_an_empty_success(monkeypatch):
    monkeypatch.setattr(replay, "MAX_LOG_RECORDS", 1)
    with pytest.raises(ValueError, match="bounded log batch"):
        decode([original(), row(110, "结果", parent=100)])


@pytest.mark.parametrize("changes", [{"game_chats": None}, {"game_chats": {True}}, {"game_bots": {99.0}}])
def test_bad_replay_trust_sets_raise_an_explicit_validation_error(changes):
    with pytest.raises(ValueError, match="trust sets"):
        decode([], **changes)


@pytest.mark.parametrize("bad_sender", [True, 1.0])
@pytest.mark.parametrize("reverse", [False, True])
def test_equal_but_differently_typed_originals_cannot_erase_a_provenance_conflict(bad_sender, reverse):
    commands = [original(sender_id=bad_sender), original(sender_id=1)]
    if reverse:
        commands.reverse()
    assert decode([*commands, original(sender_id=1), row(110, "结果", parent=100)], identity_accounts={1: 11}) == []
