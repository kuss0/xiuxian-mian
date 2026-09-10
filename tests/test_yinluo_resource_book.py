import copy
from dataclasses import replace
import itertools
import json

import pytest

from model import resource_accounting as accounting
from model import yinluo_resource_book as books
from model.verified_event import VerifiedGameEvent
from model.yinluo_resource_facts import admit_yinluo_resource_source, parse_yinluo_resource_reply


NOW = 1_800_000_000.0
CHAT = -10011
NAMES = {1001: {"provider_one", "provider_old"}, 2002: {"target_one", "target_old"}}


def point(at, msg_id, *, chat=CHAT, edited=False):
    return {"at": NOW + at, "evidence": {"source": "telegram", "chat_id": chat, "msg_id": msg_id, "edited": edited}}


def source(command, *, root=100, msg_id=110, start=110, end=120, chat=CHAT, edited=False):
    event = VerifiedGameEvent(
        event_type="edit" if edited else "message", chat_id=chat, msg_id=msg_id, sender_id=99, text="",
        reply_context={
            "reply_to_command": command, "reply_to_msg_id": root, "root_msg_id": root,
            "reply_to_sender_id": 1001, "reply_to_server_at": NOW + start, "reply_to_command_edited": False,
            "chat_id": chat,
        },
        identity_id=1001, family="", root_msg_id=root, route_source="test", reply_to_sender_id=1001,
        server_event_at=NOW + end,
    )
    result = admit_yinluo_resource_source(
        event, identity_accounts={1001: 11}, game_chats={CHAT, CHAT - 1}, game_bots={99}, now=NOW + 10000,
    )
    assert result is not None
    return result


def panel(book, *, sha=300, souls=None, at=100, msg_id=10, edited=False):
    souls = {"凶兽戾魄": 5, "妖兽精魄": 2} if souls is None else souls
    text = "【道友的阴罗幡】\n"
    if sha is not None:
        text += f"煞气池: {sha} / 1000 (30%)\n"
    if souls:
        text += "魂魄储备:\n" + "\n".join(f"- {name}: {value} 缕" for name, value in souls.items())
    result = books.stage_yinluo_panel(
        book, source(".我的阴罗幡", root=msg_id - 1, msg_id=msg_id, start=at - 1, end=at, edited=edited), text,
    )
    assert result is not None
    return result


def initial():
    book = panel(books.new_yinluo_book(1001, 11))
    cultivation = accounting.record_snapshot({}, 100000, point(100, 11))
    return books.YinluoProjection(book, cultivation)


def add(projection, command, text, *, names=NAMES, **kwargs):
    original = copy.deepcopy(projection)
    owned = source(command, **kwargs)
    result = books.stage_yinluo_reply(
        projection.book, projection.cultivation, owned, parse_yinluo_resource_reply(command, text), identity_usernames=names,
    )
    assert projection == original
    assert result is not None
    assert books.read_yinluo_book(result.book) is not None
    assert accounting.read_ledger(result.cultivation) is not None
    return result


def balance(projection, resource):
    return books.yinluo_resource_balance(projection.book, projection.cultivation, resource)


def amount(projection, resource):
    return balance(projection, resource)["value"]


CONVERSION = "【转化成功】\n你成功将 10000 点修为炼化，煞气池增加了 2000 点！"
CHARGE = "你消耗了 5000 点修为，召唤魔域的投影。"
BACKLASH = "召唤成功，镇压失败！修为暴跌了 1362 点！"
SOOTHE = "安抚成功！你消耗了 50 点修为，成功安抚了 1 个炼化槽。"


def test_two_conversions_then_replayed_first_are_two_resource_events():
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    result = add(result, ".化功为煞 10000", CONVERSION, root=200, msg_id=210, start=130, end=140)
    before = copy.deepcopy(result)
    result = add(result, ".化功为煞 10000", CONVERSION)
    assert result == before
    assert amount(result, "cultivation") == 80000
    assert amount(result, "sha") == 4300


def test_equal_message_ids_in_two_groups_do_not_share_a_resource_event():
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    result = add(result, ".化功为煞 10000", CONVERSION, chat=CHAT - 1)
    assert amount(result, "cultivation") == 80000
    assert amount(result, "sha") == 4300


def test_repeated_soothe_is_one_cultivation_charge():
    result = add(initial(), ".安抚幡灵 1", SOOTHE)
    result = add(result, ".安抚幡灵 1", SOOTHE)
    assert amount(result, "cultivation") == 99950


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_summon_charge_progress_and_backlash_commute_in_one_edited_message(order):
    frames = [
        (CHARGE, 120, False), ("魔影已降临！你开始与其进行神魂角力。", 125, True), (BACKLASH, 130, True),
    ]
    result = initial()
    for index in (*order, *reversed(order)):
        text, at, edited = frames[index]
        result = add(result, ".召唤魔影", text, end=at, edited=edited)
    assert amount(result, "cultivation") == 93638
    assert len(result.book["receipts"]) == 3
    assert len(result.cultivation["entries"]) == 2


def test_late_start_charge_resolves_a_final_results_missing_dependency():
    result = add(initial(), ".召唤魔影", BACKLASH, end=130, edited=True)
    assert balance(result, "cultivation")["status"] == "missing_effect"
    result = add(result, ".召唤魔影", CHARGE)
    assert amount(result, "cultivation") == 93638


def test_later_native_snapshot_can_cover_an_unknown_prior_charge_without_proving_the_outcome():
    result = add(initial(), ".召唤魔影", BACKLASH)
    result = replace(result, cultivation=accounting.record_snapshot(result.cultivation, 93638, point(140, 200)))
    assert amount(result, "cultivation") == 93638
    assert result.book["receipts"][0]["phase"] == "failed"


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_refine_proved_soul_debit_does_not_invent_1000_sha_cost(order):
    frames = [
        (".囚禁魂魄 1 凶兽戾魄", "一缕【凶兽戾魄】被强行打入1号炼化槽，炼化已开始。", {}),
        (".每日献祭", "你引动九幽煞气灌入幡中，煞气池增加了 500 点。", {"root": 200, "msg_id": 210, "start": 130, "end": 140}),
    ]
    result = initial()
    for index in order:
        command, text, kwargs = frames[index]
        result = add(result, command, text, **kwargs)
    assert amount(result, "soul:凶兽戾魄") == 4
    assert amount(result, "sha") is None
    result = replace(result, book=panel(result.book, sha=200, at=160, msg_id=300))
    assert amount(result, "sha") == 200


def test_native_panel_after_both_conversions_already_includes_them():
    result = initial()
    result = replace(result, book=panel(result.book, sha=4300, at=160, msg_id=300))
    result = replace(result, cultivation=accounting.record_snapshot(result.cultivation, 80000, point(160, 301)))
    result = add(result, ".化功为煞 10000", CONVERSION)
    result = add(result, ".化功为煞 10000", CONVERSION, root=200, msg_id=210, start=130, end=140)
    assert amount(result, "sha") == 4300
    assert amount(result, "cultivation") == 80000


def test_delayed_panel_before_a_conversion_preserves_the_later_sha_income():
    result = add(initial(), ".化功为煞 10000", CONVERSION, start=115)
    result = replace(result, book=panel(result.book, at=110, msg_id=50))
    assert amount(result, "sha") == 2300


def test_snapshot_overlap_cannot_guess_if_a_known_debit_is_already_included():
    result = add(initial(), ".化功为煞 10000", CONVERSION, end=130)
    result = replace(result, book=panel(result.book, sha=2300, at=120, msg_id=200))
    assert balance(result, "sha")["status"] == "overlapping_observation"


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_conversion_amount_correction_is_one_fact_in_either_delivery_order(order):
    frames = [(CONVERSION, 120, False), (CONVERSION.replace("10000", "9000").replace("2000", "1800"), 130, True)]
    result = initial()
    for index in order:
        text, at, edited = frames[index]
        result = add(result, ".化功为煞 10000", text, end=at, edited=edited)
    assert amount(result, "cultivation") == 91000
    assert amount(result, "sha") == 2100


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_bonus_removed_by_newer_edit_is_unknown_even_if_old_bonus_arrives_last(order):
    frames = [(CONVERSION + "额外获得了 100 点精纯煞气。", 120, False), (CONVERSION, 130, True)]
    result = initial()
    for index in order:
        text, at, edited = frames[index]
        result = add(result, ".化功为煞 10000", text, end=at, edited=edited)
    assert amount(result, "sha") is None
    assert amount(result, "cultivation") == 90000


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_empty_edit_invalidates_the_final_scope_but_not_the_separate_start_charge(order):
    frames = [(CHARGE, 120, False), (BACKLASH, 130, True), ("", 140, True)]
    result = initial()
    for index in order:
        text, at, edited = frames[index]
        result = add(result, ".召唤魔影", text, end=at, edited=edited)
    entries = {item["key"].partition(":")[0]: item for item in result.cultivation["entries"]}
    assert entries["yinluo_summon_cost"]["amount"] == -5000
    assert not entries["yinluo_summon_cost"]["conflicted"]
    assert entries["yinluo_summon_backlash"]["conflicted"]
    assert amount(result, "cultivation") is None


@pytest.mark.parametrize("order", list(itertools.permutations(range(3))))
def test_final_repetition_of_start_cost_does_not_make_it_part_of_a_withdrawn_outcome(order):
    frames = [(CHARGE, 120, False), (CHARGE + BACKLASH, 130, True), ("", 140, True)]
    result = initial()
    for index in order:
        text, at, edited = frames[index]
        result = add(result, ".召唤魔影", text, end=at, edited=edited)
    entries = {item["key"].partition(":")[0]: item for item in result.cultivation["entries"]}
    assert entries["yinluo_summon_cost"]["amount"] == -5000
    assert not entries["yinluo_summon_cost"]["conflicted"]
    assert entries["yinluo_summon_backlash"]["conflicted"]


def test_strip_accounts_for_sha_and_provider_backlash_in_one_projection():
    result = add(
        initial(), ".剥离咒源 @target_one",
        "【剥离咒源失败】\n@provider_one 以阴罗幡镇咒，阴罗幡煞气被吞去 120 点，@provider_one 修为折损 500，@target_one 魂封 +4。",
    )
    assert amount(result, "sha") == 180
    assert amount(result, "cultivation") == 99500


def test_strip_cannot_skip_named_actor_validation_when_called_directly():
    result = add(
        initial(), ".剥离咒源 @target_one",
        "【剥离咒源失败】\n@other_provider 以阴罗幡镇咒，阴罗幡煞气被吞去 120 点，@other_provider 修为折损 500，@target_one 魂封 +4。",
    )
    assert amount(result, "sha") is amount(result, "cultivation") is None


def test_duel_cultivation_entries_are_preserved_while_yinluo_is_reprojected():
    result = initial()
    result = replace(result, cultivation=accounting.record_delta(result.cultivation, "duel:command:30", -60000, point(105, 30), point(109, 40)))
    duel = copy.deepcopy(result.cultivation["entries"][0])
    result = add(result, ".化功为煞 10000", CONVERSION)
    result = add(result, ".安抚幡灵 1", SOOTHE, root=200, msg_id=210, start=130, end=140)
    assert amount(result, "cultivation") == 29950
    assert next(item for item in result.cultivation["entries"] if item["key"] == duel["key"]) == duel


def test_known_denial_resolves_missing_dependency_without_creating_zero_cost_fact():
    result = add(initial(), ".召唤魔影", "魔影已降临！你开始与其进行神魂角力。")
    result = add(result, ".召唤魔影", "魔域裂隙尚未平复，请稍后再来。", end=130, edited=True)
    assert amount(result, "cultivation") == 100000
    assert not result.cultivation["entries"]


def test_denial_after_an_explicit_charge_is_not_an_automatic_refund():
    result = add(initial(), ".召唤魔影", CHARGE)
    result = add(result, ".召唤魔影", "魔域裂隙尚未平复，请稍后再来。", end=130, edited=True)
    assert amount(result, "cultivation") is None
    assert result.cultivation["entries"]


def test_sparse_native_panel_keeps_unmentioned_soul_balance():
    result = initial()
    result = replace(result, book=panel(result.book, sha=None, souls={"妖兽精魄": 3}, at=130, msg_id=200))
    assert amount(result, "sha") == 300
    assert amount(result, "soul:凶兽戾魄") == 5
    assert amount(result, "soul:妖兽精魄") == 3


def test_json_roundtrip_preserves_facts_and_never_reapplies_costs():
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    restored = books.YinluoProjection(json.loads(json.dumps(result.book)), json.loads(json.dumps(result.cultivation)))
    assert books.read_yinluo_book(restored.book) is not None
    assert add(restored, ".化功为煞 10000", CONVERSION) == restored
    assert amount(restored, "sha") == 2300


@pytest.mark.parametrize("replacement", [None, {}, [], {"version": True}])
def test_legacy_or_corrupt_book_never_looks_like_empty_valid_history(replacement):
    assert books.read_yinluo_book(replacement) is None
    assert books.yinluo_resource_balance(replacement, {}, "sha") == {"status": "corrupt", "value": None}


@pytest.mark.parametrize("changes", [{"account_id": 22}, {"identity_id": 2002}, {"identity_id": True}, {"command": None}])
def test_old_or_replaced_owner_cannot_write_a_new_book(changes):
    result = initial()
    owned = replace(source(".化功为煞 10000"), **changes)
    assert books.stage_yinluo_reply(result.book, result.cultivation, owned, parse_yinluo_resource_reply(".化功为煞 10000", CONVERSION), identity_usernames=NAMES) is None


def test_orphaned_cultivation_delta_is_not_silently_removed_on_first_book_use():
    result = initial()
    owned = source(".化功为煞 10000")
    ledger = accounting.record_delta(result.cultivation, owned.component_key("convert_cost"), -10000, owned.start, owned.end)
    assert books.stage_yinluo_reply(result.book, ledger, owned, parse_yinluo_resource_reply(owned.command.text, CONVERSION), identity_usernames=NAMES) is None


def test_receipt_capacity_retains_all_existing_facts_and_blocks_projection(monkeypatch):
    monkeypatch.setattr(books, "MAX_RECEIPTS", 1)
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    previous = copy.deepcopy(result)
    result = add(result, ".安抚幡灵 1", SOOTHE, root=200, msg_id=210, start=130, end=140)
    assert result.book["receipts"] == previous.book["receipts"]
    assert result.cultivation == previous.cultivation
    assert balance(result, "sha")["status"] == "capacity"
    assert balance(result, "cultivation")["status"] == "capacity"


def test_rebinding_one_result_message_to_another_command_blocks_without_transferring_cost():
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    old = copy.deepcopy(result.cultivation)
    result = add(result, ".安抚幡灵 1", SOOTHE, root=90, start=108, end=130, edited=True)
    assert result.cultivation == old
    assert balance(result, "cultivation")["status"] == "command_conflict"


def test_corrupt_receipt_amount_type_or_sign_cannot_become_verified_credit():
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    for invalid in (True, "10000", 10000, float("nan"), -(accounting.MAX_BALANCE + 1)):
        book = copy.deepcopy(result.book)
        book["receipts"][0]["effects"][0]["amount"] = invalid
        assert books.read_yinluo_book(book) is None


def test_preexisting_delta_cannot_borrow_an_unproved_pending_requirement():
    result = add(initial(), ".召唤魔影", "魔影已降临！你开始与其进行神魂角力。")
    owned = source(".召唤魔影")
    ledger = accounting.record_delta(result.cultivation, owned.component_key("summon_cost"), -5000, owned.start, owned.end)
    assert books.stage_yinluo_reply(result.book, ledger, owned, parse_yinluo_resource_reply(owned.command.text, CHARGE), identity_usernames=NAMES) is None
    assert books.yinluo_resource_balance(result.book, ledger, "cultivation")["status"] == "unowned_cultivation"


def test_unknown_soul_type_blocks_other_stocks_until_named_or_observed():
    result = add(initial(), ".召唤魔影", "召唤成功，镇压成功！所得魂魄未明。")
    assert balance(result, "soul:妖兽精魄")["status"] == "unknown_soul_effect"
    result = add(result, ".召唤魔影", "召唤成功，镇压成功！留下了一道精纯的【凶兽戾魄】。", end=130, edited=True)
    assert amount(result, "soul:妖兽精魄") == 2
    assert amount(result, "soul:凶兽戾魄") == 6


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_removed_soul_name_blocks_other_stocks_not_only_the_old_named_type(order):
    frames = [("召唤成功，镇压成功！留下了一道精纯的【凶兽戾魄】。", 120, False), ("", 130, True)]
    result = initial()
    for index in order:
        text, at, edited = frames[index]
        result = add(result, ".召唤魔影", text, end=at, edited=edited)
    assert amount(result, "soul:妖兽精魄") is None
    assert amount(result, "soul:凶兽戾魄") is None
    result = replace(result, book=panel(result.book, at=140, msg_id=200, sha=None, souls={"妖兽精魄": 2}))
    assert amount(result, "soul:妖兽精魄") == 2
    assert amount(result, "soul:凶兽戾魄") is None


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_changed_soul_name_does_not_leave_both_old_and_new_rewards_spendable(order):
    frames = [("凶兽戾魄", 120, False), ("妖兽精魄", 130, True)]
    result = initial()
    for index in order:
        name, at, edited = frames[index]
        result = add(result, ".召唤魔影", f"召唤成功，镇压成功！留下了一道精纯的【{name}】。", end=at, edited=edited)
    assert amount(result, "soul:凶兽戾魄") is None
    assert amount(result, "soul:妖兽精魄") == 3


def test_forest_known_reward_does_not_require_an_invented_sha_debit():
    result = add(initial(), ".血洗山林", "【血洗功成】成功捕获了 2 缕【妖兽精魄】。")
    assert amount(result, "soul:妖兽精魄") == 4
    assert amount(result, "sha") == 300
    assert not result.book["ledgers"]["sha"]["entries"]


def test_same_second_conflicting_cost_does_not_gain_order_from_arrival_time():
    result = add(initial(), ".安抚幡灵 1", SOOTHE, edited=True)
    result = add(result, ".安抚幡灵 1", SOOTHE.replace("50", "60"), edited=True)
    assert amount(result, "cultivation") is None
    result = add(result, ".安抚幡灵 1", SOOTHE.replace("50", "60"), end=130, edited=True)
    assert amount(result, "cultivation") == 99940


def test_api_evidence_cannot_be_supplied_as_a_native_panel_clock():
    result = initial()
    owned = replace(source(".我的阴罗幡"), result_at="1800000120")
    assert books.stage_yinluo_panel(result.book, owned, "【道友的阴罗幡】\n煞气池: 9999 / 1000 (100%)") is None


@pytest.mark.parametrize("changes", [
    {"scopes": []}, {"phase": "denied"}, {"scopes": ["outcome", "outcome"]},
    {"required": [["convert_cost", "sha"]]}, {"sender_id": True},
    {"end": point(100, 99)}, {"start": point(110, 100, edited=True)},
])
def test_corrupt_receipt_provenance_or_revision_scope_is_rejected(changes):
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    result.book["receipts"][0].update(changes)
    assert books.read_yinluo_book(result.book) is None


@pytest.mark.parametrize("target", ["cultivation", "sha"])
def test_projected_cache_is_not_authority_when_it_disagrees_with_the_facts(target):
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    ledger = result.cultivation if target == "cultivation" else result.book["ledgers"][target]
    ledger["entries"][0]["amount"] = 90000
    assert balance(result, target)["status"] == "inconsistent_projection"
    repaired = add(result, ".化功为煞 10000", CONVERSION)
    assert amount(repaired, "cultivation") == 90000
    assert amount(repaired, "sha") == 2300


def test_lost_cached_debit_is_not_an_empty_verified_projection():
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    result.cultivation["entries"] = []
    assert balance(result, "cultivation")["status"] == "inconsistent_projection"


def test_missing_required_component_in_a_persisted_receipt_is_not_normalized_away():
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    result.book["receipts"][0]["required"] = []
    assert books.read_yinluo_book(result.book) is None


def contextless_edit(**changes):
    defaults = {
        "event_type": "edit", "chat_id": CHAT, "msg_id": 110, "sender_id": 99,
        "text": CONVERSION.replace("2000", "20000"), "reply_context": None, "identity_id": 0,
        "family": "", "root_msg_id": 0, "route_source": "test", "reply_to_sender_id": 0,
        "server_event_at": NOW + 130,
    }
    return VerifiedGameEvent(**{**defaults, **changes})


def invalidate(projection, event=None, **kwargs):
    return books.stage_unresolved_yinluo_edit(
        projection.book, projection.cultivation, event or contextless_edit(),
        **{"identity_accounts": {1001: 11}, "game_chats": {CHAT}, "game_bots": {99}, "now": NOW + 10000, **kwargs},
    )


def test_retained_native_source_can_invalidate_a_contextless_edit_without_crediting_new_text():
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    previous = copy.deepcopy(result)
    updated = invalidate(result)
    assert result == previous
    assert amount(updated, "sha") is amount(updated, "cultivation") is None
    assert updated.book["receipts"][-1]["effects"] == []
    assert updated.book["identity_id"] == 1001


def test_conflicting_routing_hints_can_only_invalidate_the_known_owner_not_credit_another_identity():
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    event = contextless_edit(identity_id=2002, reply_context={"send_as_id": 2002, "account_id": 22})
    updated = invalidate(result, event)
    assert updated.book["identity_id"] == 1001
    assert amount(updated, "sha") is None


@pytest.mark.parametrize("changes", [
    {"sender_id": 123}, {"sender_id": True}, {"chat_id": CHAT - 1}, {"msg_id": 111},
    {"event_type": "message"}, {"server_event_at": 0}, {"server_event_at": str(NOW + 130)},
    {"server_event_at": NOW + 20000}, {"msg_id": True},
])
def test_unowned_or_unsupported_contextless_edit_cannot_invalidate_another_result(changes):
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    assert invalidate(result, contextless_edit(**changes)) is None


@pytest.mark.parametrize("accounts", [{1001: 22}, {1001: True}, {}, {2002: 11}, {1001: 11, True: 22}])
def test_contextless_edit_does_not_cross_account_rebinding(accounts):
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    assert invalidate(result, identity_accounts=accounts) is None


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_same_native_edit_clock_with_different_text_stays_unknown(order):
    frames = [CONVERSION, ""]
    result = initial()
    for index in order:
        result = add(result, ".化功为煞 10000", frames[index], edited=True)
    assert amount(result, "sha") is None
    assert amount(result, "cultivation") is None


def test_contextless_edit_cannot_supply_positive_amount_on_a_new_message():
    result = initial()
    assert invalidate(result) is None
    assert amount(result, "sha") == 300


def test_source_capacity_remains_held_until_a_native_baseline_covers_it():
    result = initial()
    for index in range(accounting.MAX_SOURCES_PER_ENTRY + 1):
        result = add(result, ".安抚幡灵 1", SOOTHE, msg_id=110 + index, end=120 + index)
    assert balance(result, "cultivation")["status"] == "capacity"
    result = replace(result, cultivation=accounting.record_snapshot(result.cultivation, 99950, point(150, 200)))
    assert amount(result, "cultivation") == 99950


def test_receipt_capacity_does_not_forget_unresolved_ownership_after_a_panel(monkeypatch):
    monkeypatch.setattr(books, "MAX_RECEIPTS", 1)
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    result = add(result, ".安抚幡灵 1", SOOTHE, root=200, msg_id=210, start=130, end=140)
    result = replace(result, book=panel(result.book, sha=2300, at=160, msg_id=300))
    assert balance(result, "sha")["status"] == "capacity"


def test_full_receipt_budget_rebuilds_one_atomic_projection_without_eviction():
    result = initial()
    result = replace(result, cultivation=accounting.record_snapshot({}, 10000000, point(100, 11)))
    template = add(initial(), ".化功为煞 10000", CONVERSION).book["receipts"][0]
    for index in range(books.MAX_RECEIPTS - 1):
        receipt = copy.deepcopy(template)
        receipt["start"] = point(110 + 2 * index, 100 + 20 * index)
        receipt["end"] = point(120 + 2 * index, 110 + 20 * index)
        result.book["receipts"].append(receipt)
    last = books.MAX_RECEIPTS - 1
    result = add(
        result, ".化功为煞 10000", CONVERSION,
        root=100 + 20 * last, msg_id=110 + 20 * last, start=110 + 2 * last, end=120 + 2 * last,
    )
    assert len(result.book["receipts"]) == books.MAX_RECEIPTS
    assert len(result.cultivation["entries"]) == books.MAX_RECEIPTS
    assert amount(result, "cultivation") == 10000000 - 10000 * books.MAX_RECEIPTS
    assert amount(result, "sha") == 300 + 2000 * books.MAX_RECEIPTS


def test_shared_duel_writer_can_append_after_yinluo_without_false_cache_conflict():
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    result = replace(result, cultivation=accounting.record_delta(result.cultivation, "duel:command:30", -60000, point(125, 200), point(130, 210)))
    assert amount(result, "cultivation") == 30000
    assert amount(result, "sha") == 2300


def test_shared_ledger_retirement_of_covered_yinluo_fact_is_not_a_missing_debit():
    result = add(initial(), ".化功为煞 10000", CONVERSION)
    result = replace(result, cultivation=accounting.record_snapshot(result.cultivation, 90000, point(130, 200)))
    result.cultivation["entries"] = []
    assert amount(result, "cultivation") == 90000


def test_unowned_sha_delta_cannot_be_mixed_with_the_book():
    result = initial()
    result.book["ledgers"]["sha"] = accounting.record_delta(result.book["ledgers"]["sha"], "legacy:1", 90000, point(110, 100), point(120, 110))
    assert books.read_yinluo_book(result.book) is None


def test_shared_cultivation_capacity_keeps_uncovered_foreign_entries(monkeypatch):
    monkeypatch.setattr(accounting, "MAX_ENTRIES", 2)
    result = initial()
    for index in range(2):
        result = replace(result, cultivation=accounting.record_delta(
            result.cultivation, f"duel:command:{index}", -1000,
            point(102 + index * 2, 20 + 2 * index), point(103 + index * 2, 21 + 2 * index),
        ))
    foreign = copy.deepcopy(result.cultivation["entries"])
    result = add(result, ".化功为煞 10000", CONVERSION)
    assert result.cultivation["entries"] == foreign
    assert balance(result, "cultivation")["status"] == "capacity"


def test_shared_cultivation_capacity_can_retire_only_a_covered_entry(monkeypatch):
    monkeypatch.setattr(accounting, "MAX_ENTRIES", 2)
    result = initial()
    for index in range(2):
        result = replace(result, cultivation=accounting.record_delta(
            result.cultivation, f"duel:command:{index}", -1000,
            point(102 + index * 2, 20 + 2 * index), point(103 + index * 2, 21 + 2 * index),
        ))
    result = replace(result, cultivation=accounting.record_snapshot(result.cultivation, 98000, point(108, 50)))
    result = add(result, ".化功为煞 10000", CONVERSION)
    assert len(result.cultivation["entries"]) == 2
    assert amount(result, "cultivation") == 88000
