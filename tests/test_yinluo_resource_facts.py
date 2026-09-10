from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model.real_message_replay import get_real_message_text
from model.resource_accounting import MAX_BALANCE, valid_point
from model.verified_event import VerifiedGameEvent
from model.yinluo_resource_facts import (
    admit_yinluo_resource_source,
    parse_yinluo_resource_command,
    parse_yinluo_resource_panel,
    parse_yinluo_resource_reply,
    qualify_yinluo_resource_reply,
)


FIXTURES = Path(__file__).parent / "fixtures" / "real_message_samples.json"
NOW = 1_800_000_000.0


def sample(key):
    return get_real_message_text(FIXTURES, key)


def values(reply):
    return {(item.component, item.resource, item.subject): item.amount for item in reply.effects}


def event(command=".化功为煞 10000", **changes):
    source = VerifiedGameEvent(
        event_type="message", chat_id=-10011, msg_id=110, sender_id=99,
        text="【转化成功】\n你成功将 10000 点修为炼化，煞气池增加了 2000 点！",
        reply_context={
            "reply_to_command": command, "reply_to_msg_id": 100,
            "reply_to_sender_id": 1001, "reply_to_server_at": NOW - 10,
            "reply_to_command_edited": False,
            "root_msg_id": 100, "chat_id": -10011,
        },
        identity_id=0, family="yinluo_convert", root_msg_id=100,
        route_source="message:reply_context", reply_to_sender_id=1001,
        server_event_at=NOW,
    )
    return replace(source, **changes)


def admit(source, **changes):
    args = {"identity_accounts": {1001: 11}, "game_chats": {-10011, -10022}, "game_bots": {99}, "now": NOW}
    args.update(changes)
    return admit_yinluo_resource_source(source, **args)


@pytest.mark.parametrize("command, action", [
    (".我的阴罗幡", "banner"), (".化功为煞 10000", "convert"),
    (".每日献祭", "sacrifice"), (".安抚幡灵 3", "soothe"),
    (".召唤魔影", "summon"), (".血洗山林", "forest"),
    (".囚禁魂魄 1 凶兽戾魄", "refine"), (".收取精华", "collect"),
    (".收取精华 9", "collect"), (".借幡镇魂 @target_one", "assist_banner"),
    (".剥离咒源 @target_one", "assist_strip"),
])
def test_supported_resource_commands(command, action):
    parsed = parse_yinluo_resource_command(command)
    assert parsed is not None
    assert parsed.action == action
    assert parsed.text == command


@pytest.mark.parametrize("command", [
    None, True, 1000, "", ".闭关", ".化功为煞", ".化功为煞 0", ".化功为煞 -1",
    ".化功为煞 1e3", ".化功为煞 1000\n.每日献祭", ".化功为煞 1000 extra",
    f".化功为煞 {MAX_BALANCE + 1}", ".每日献祭 extra", ".我的阴罗幡 extra",
    ".囚禁魂魄 1", ".囚禁魂魄 0 凶兽戾魄", ".囚禁魂魄 100 凶兽戾魄",
    ".囚禁魂魄 1 .下咒", ".安抚幡灵 1 extra", ".收取精华 1 2",
    ".剥离咒源", ".借幡镇魂 target", ".剥离咒源 @target extra",
])
def test_unsupported_or_ambiguous_commands_are_not_financial_authority(command):
    assert parse_yinluo_resource_command(command) is None


def test_conversion_is_one_cost_with_separate_income_and_bonus():
    result = parse_yinluo_resource_reply(
        ".化功为煞 10000",
        "【转化成功】\n你成功将 10000 点修为炼化，煞气池增加了 2000 点！\n额外获得了 100 点精纯煞气。",
    )
    assert result.phase == "success"
    assert values(result) == {
        ("convert_cost", "cultivation", "actor"): -10000,
        ("convert_sha", "sha", "actor"): 2000,
        ("convert_bonus", "sha", "actor"): 100,
    }
    assert result.issues == ()


def test_conversion_backlash_does_not_charge_the_same_lost_cost_twice():
    result = parse_yinluo_resource_reply(
        ".化功为煞 10000",
        "【转化失败·反噬】\n魔功失控，你消耗的 10000 点修为尽数逸散！\n你受到了【煞气反噬】。",
    )
    assert result.phase == "failed"
    assert values(result) == {("convert_cost", "cultivation", "actor"): -10000}
    assert result.required == (("convert_cost", "cultivation"),)


def test_requested_conversion_amount_is_not_a_confirmed_cost():
    result = parse_yinluo_resource_reply(".化功为煞 1000", "你开始运转魔功，试图将 1000 点修为凝练为纯粹的煞气...")
    assert result.phase == "pending"
    assert not result.effects
    assert ("convert_cost", "cultivation") in result.required


@pytest.mark.parametrize("amount", ["未知", "-50", "+50", "1.5", "1e3", "50junk", "５０", "50万", str(MAX_BALANCE + 1)])
def test_unknown_cost_is_not_assumed_zero_or_a_default(amount):
    result = parse_yinluo_resource_reply(".安抚幡灵 1", f"安抚成功！\n你消耗了 {amount} 点修为，成功安抚了 1 个炼化槽。")
    assert result.phase == "success"
    assert values(result) == {("soothe_cost", "cultivation", "actor"): None}
    assert result.issues == ("soothe_cost_amount_unknown",)


def test_soothe_literal_zero_is_a_real_observation():
    result = parse_yinluo_resource_reply(".安抚幡灵 1", "安抚成功！\n你消耗了 0 点修为，成功安抚了 1 个炼化槽。")
    assert values(result) == {("soothe_cost", "cultivation", "actor"): 0}
    assert not result.issues


def test_missing_or_repeated_cost_cannot_borrow_a_default():
    for text in (
        "安抚成功！\n成功安抚了 1 个炼化槽。",
        "安抚成功！\n你消耗了 50 点修为，成功安抚了 1 个炼化槽。\n你消耗了 60 点修为。",
    ):
        result = parse_yinluo_resource_reply(".安抚幡灵 1", text)
        assert values(result) == {("soothe_cost", "cultivation", "actor"): None}


def test_sacrifice_uses_reported_primary_and_bonus_income():
    result = parse_yinluo_resource_reply(".每日献祭", sample("yinluo.daily_sacrifice.success"))
    assert result.phase == "success"
    assert values(result) == {
        ("sacrifice_sha", "sha", "actor"): 575,
        ("sacrifice_bonus", "sha", "actor"): 75,
    }


def test_summon_final_does_not_erase_or_repeat_the_start_cost():
    start = parse_yinluo_resource_reply(".召唤魔影", "你消耗了 5000 点修为，开始召唤魔域的投影。")
    fighting = parse_yinluo_resource_reply(".召唤魔影", sample("yinluo.demon_summon.pending_fight"))
    failed = parse_yinluo_resource_reply(".召唤魔影", sample("yinluo.demon_summon.failed_backlash"))
    assert start.phase == fighting.phase == "pending"
    assert values(start) == {("summon_cost", "cultivation", "actor"): -5000}
    assert not fighting.effects
    assert values(failed) == {("summon_backlash", "cultivation", "actor"): -1362}
    assert ("summon_cost", "cultivation") in failed.required
    assert {item.component for item in failed.effects} == {"summon_backlash"}
    assert start.scopes == ("charge",)
    assert fighting.scopes == ()
    assert failed.scopes == ("outcome",)


def test_summon_combined_receipt_has_two_independent_revision_scopes():
    result = parse_yinluo_resource_reply(
        ".召唤魔影", "你消耗了 5000 点修为，召唤魔域的投影。\n" + sample("yinluo.demon_summon.failed_backlash"),
    )
    assert result.scopes == ("charge", "outcome")
    assert {effect.component: effect.scope for effect in result.effects} == {
        "summon_cost": "charge", "summon_backlash": "outcome",
    }


def test_summon_success_keeps_a_missing_start_dependency_visible():
    result = parse_yinluo_resource_reply(".召唤魔影", sample("yinluo.demon_summon.success"))
    assert result.phase == "success"
    assert values(result) == {("summon_soul", "soul:凶兽戾魄", "actor"): 1}
    assert ("summon_cost", "cultivation") in result.required


def test_refine_success_does_not_treat_an_estimate_as_observed_sha_cost():
    result = parse_yinluo_resource_reply(
        ".囚禁魂魄 7 凶兽戾魄",
        "一缕【凶兽戾魄】被强行打入7号炼化槽，在煞气的包裹下发出阵阵哀嚎，炼化已开始。",
    )
    assert result.phase == "success"
    assert values(result) == {("refine_soul", "soul:凶兽戾魄", "actor"): -1}
    assert ("refine_sha", "sha") in result.required


@pytest.mark.parametrize("command", [".囚禁魂魄 1 凶兽戾魄", ".囚禁魂魄 7 妖兽精魄"])
def test_refine_wrong_slot_or_resource_is_not_a_matching_outcome(command):
    result = parse_yinluo_resource_reply(command, "一缕【凶兽戾魄】被强行打入7号炼化槽，炼化已开始。")
    assert result.phase == "conflict"
    assert not result.effects
    assert "refine_target_mismatch" in result.issues


def test_collection_lineage_is_not_unrefined_stock_income():
    result = parse_yinluo_resource_reply(
        ".收取精华 1",
        "收取成功！\n你从 1 个炼化槽中获得了: 【四级妖丹】x1！\n阴罗幡吞纳残魄，幡魂谱系精进: 凶兽戾魄+1。",
    )
    assert result.phase == "success"
    assert not result.effects
    assert not result.required


def test_forest_soul_rewards_do_not_prove_a_zero_sha_cost():
    result = parse_yinluo_resource_reply(
        ".血洗山林", "【血洗功成】\n成功捕获了 2 缕【妖兽精魄】，额外拘来 1 缕【凶兽戾魄】。",
    )
    assert result.phase == "success"
    assert values(result) == {
        ("forest_soul", "soul:妖兽精魄", "actor"): 2,
        ("forest_bonus_soul", "soul:凶兽戾魄", "actor"): 1,
    }
    assert ("forest_sha", "sha") not in result.required


def test_explicit_forest_cost_is_recorded_without_inventing_a_fixed_price():
    result = parse_yinluo_resource_reply(
        ".血洗山林", "你消耗了 170 点煞气，催动煞气，前往山脉扫荡。",
    )
    assert values(result) == {("forest_sha", "sha", "actor"): -170}
    assert ("forest_sha", "sha") in result.required


def test_actual_conversion_cost_overrules_the_requested_amount():
    result = parse_yinluo_resource_reply(
        ".化功为煞 10000", "【转化成功】\n你成功将 9000 点修为炼化，煞气池增加了 1800 点！",
    )
    assert values(result) == {
        ("convert_cost", "cultivation", "actor"): -9000,
        ("convert_sha", "sha", "actor"): 1800,
    }
    assert result.issues == ("convert_requested_amount_differs",)


def test_strip_failure_reports_both_resources_without_charging_the_beneficiary():
    result = parse_yinluo_resource_reply(
        ".剥离咒源 @target_one",
        "【剥离咒源失败】\n封魂咒骤然反扑，阴罗幡煞气被吞去 120 点，@provider_one 修为折损 500，@target_one 魂封 +4。",
    )
    assert result.phase == "failed"
    assert values(result) == {
        ("assist_sha", "sha", "actor"): -120,
        ("assist_backlash", "cultivation", "@provider_one"): -500,
    }


def test_strip_success_without_cost_text_cannot_invent_120_sha():
    result = parse_yinluo_resource_reply(
        ".剥离咒源 @target_one",
        "【剥离咒源成功】\n@provider_one 以阴罗幡截住咒源反噬，替 @target_one 剥下一段阴罗残咒。",
    )
    assert result.phase == "success"
    assert not result.effects
    assert result.required == (("assist_sha", "sha"),)
    assert result.actor_username == "provider_one"
    assert result.target_username == "target_one"


@pytest.mark.parametrize("key, command, expected", [
    ("wanxin.assist_banner.real_408208", ".借幡镇魂 @WalterWA2000", -80),
    ("wanxin.assist_strip.real_408224", ".剥离咒源 @WalterWA2000", None),
])
def test_real_assist_reply_keeps_named_actor_and_target_as_separate_evidence(key, command, expected):
    reply = parse_yinluo_resource_reply(command, sample(key))
    assert reply.phase == "success"
    assert reply.actor_username == "sanshaoyedejian1"
    assert reply.target_username == "walterwa2000"
    assert next((effect.amount for effect in reply.effects if effect.component == "assist_sha"), None) == expected


@pytest.mark.parametrize("text", [
    "@provider_one 借阴罗幡，幡面煞气被削去 80 点。\n@provider_two 借阴罗幡。\n@target_one 魂封 -1。",
    "@provider_one 借阴罗幡，幡面煞气被削去 80 点。\n@target_one 魂封 -1。\n@target_two 魂封 -2。",
])
def test_ambiguous_named_participants_do_not_become_an_actor_debit(text):
    reply = parse_yinluo_resource_reply(".借幡镇魂 @target_one", "【借幡镇魂】\n" + text)
    assert reply.phase == "conflict"
    assert not reply.effects
    assert "conflicting_assist_participants" in reply.issues


@pytest.mark.parametrize("command, text", [
    (".囚禁魂魄 1 凶兽戾魄", "你的煞气不足！炼化需要消耗 1000 点煞气。"),
    (".借幡镇魂 @target", "你的阴罗幡煞气不足，借幡镇魂至少需要 80 点煞气。"),
    (".剥离咒源 @target", "你的阴罗幡煞气不足，剥离咒源至少需要 120 点煞气。"),
    (".召唤魔影", "魔域裂隙尚未平复，请在 7小时 后再行召唤。"),
    (".每日献祭", "今日已献祭，幡灵已饱。"),
    (".化功为煞 10000", "你刚施展过此术，经脉尚在恢复，请在 1小时 后再试。"),
])
def test_denial_quantities_are_not_resource_debits(command, text):
    result = parse_yinluo_resource_reply(command, text)
    assert result.phase == "denied"
    assert not result.effects
    assert not result.required


def test_unrelated_retreat_bonus_is_not_counted_by_yinluo_again():
    assert parse_yinluo_resource_reply(".深度闭关", sample("yinluo.retreat.success_bonus")) is None
    result = parse_yinluo_resource_reply(".化功为煞 10000", sample("yinluo.retreat.success_bonus"))
    assert result.phase == "unknown"
    assert not result.effects


def test_owned_source_requires_native_command_and_preserves_component_identity():
    source = admit(event())
    assert source is not None
    assert (source.identity_id, source.account_id) == (1001, 11)
    assert valid_point(source.start, telegram_only=True)
    assert valid_point(source.end, telegram_only=True)
    assert source.component_key("convert_cost") != source.component_key("convert_sha")
    edited = admit(event(event_type="edit", server_event_at=NOW + 1))
    assert edited.component_key("convert_cost") == source.component_key("convert_cost")
    assert edited.end["evidence"]["edited"]
    assert edited.start == source.start


def test_two_chats_with_equal_ids_are_distinct_operations():
    first = event()
    second = replace(first, chat_id=-10022, reply_context={**first.reply_context, "chat_id": -10022})
    assert admit(first).component_key("convert_cost") != admit(second).component_key("convert_cost")


def test_channel_identity_cannot_fall_back_to_its_account_or_another_role():
    source = event(reply_to_sender_id=-1001001)
    source = replace(source, reply_context={**source.reply_context, "reply_to_sender_id": -1001001})
    assert admit(source).identity_id == 1001
    assert admit(source, identity_accounts={1001: 11, 1001001: 11}) is None
    assert admit(source, identity_accounts={11: 11}) is None


@pytest.mark.parametrize("changes", [
    {"sender_id": 88}, {"sender_id": True}, {"chat_id": -10033}, {"msg_id": 0},
    {"msg_id": True}, {"msg_id": 99}, {"root_msg_id": 101}, {"root_msg_id": True},
    {"event_type": "broadcast"}, {"server_event_at": 0}, {"server_event_at": True},
    {"server_event_at": NOW + 100}, {"server_event_at": float("nan")},
    {"server_event_at": float("inf")}, {"identity_id": 2002}, {"identity_id": True},
    {"reply_context": None},
])
def test_unowned_or_non_native_event_is_rejected(changes):
    assert admit(event(**changes)) is None


@pytest.mark.parametrize("changes", [
    {"reply_to_command": ".闭关"}, {"reply_to_command_edited": True},
    {"reply_to_msg_id": 109, "root_msg_id": 100}, {"reply_to_msg_id": True},
    {"reply_to_sender_id": 99}, {"reply_to_sender_id": 2002}, {"reply_to_sender_id": 0},
    {"reply_to_server_at": 0}, {"reply_to_server_at": NOW + 1},
    {"chat_id": -10022}, {"send_as_id": 2002}, {"account_id": 22},
])
def test_context_hints_do_not_override_the_original_command_owner(changes):
    source = event()
    assert admit(replace(source, reply_context={**source.reply_context, **changes})) is None


def test_local_log_time_cannot_fill_in_the_missing_native_command_time():
    source = event()
    context = {key: value for key, value in source.reply_context.items() if key != "reply_to_server_at"}
    context.update(ts_epoch=NOW - 10, time=NOW - 10, sent_at=NOW - 10)
    assert admit(replace(source, reply_context=context)) is None


@pytest.mark.parametrize("key", ["chat_id", "root_msg_id", "account_id", "send_as_id"])
@pytest.mark.parametrize("kind", [bool, float, str])
def test_context_hints_must_have_integer_types_even_when_equal(key, kind):
    source = event(identity_id=1)
    source = replace(source, reply_to_sender_id=1, reply_context={
        **source.reply_context, "reply_to_sender_id": 1,
    })
    expected = {"chat_id": -10011, "root_msg_id": 100, "account_id": 1, "send_as_id": 1}
    context = {**source.reply_context, key: kind(expected[key])}
    assert admit(replace(source, reply_context=context), identity_accounts={1: 1}) is None


@pytest.mark.parametrize("hint", [None, 0])
def test_unresolved_identity_hint_does_not_reject_a_verified_original_sender(hint):
    source = event()
    source = replace(source, reply_context={**source.reply_context, "send_as_id": hint})
    assert admit(source).identity_id == 1001


@pytest.mark.parametrize("sender", [2002, 0, True, 1001.0, "1001", None])
def test_conflicting_or_invalid_event_sender_cannot_borrow_context_ownership(sender):
    assert admit(event(reply_to_sender_id=sender)) is None


@pytest.mark.parametrize("stamp", [True, str(NOW - 10), None, float("inf"), float("nan")])
def test_command_time_is_native_numeric_evidence(stamp):
    source = event()
    source = replace(source, reply_context={**source.reply_context, "reply_to_server_at": stamp})
    assert admit(source) is None


def test_missing_original_edit_provenance_is_not_proof_of_an_unedited_command():
    source = event()
    context = dict(source.reply_context)
    context.pop("reply_to_command_edited")
    assert admit(replace(source, reply_context=context)) is None


@pytest.mark.parametrize("changes", [
    {"game_chats": None}, {"game_chats": {-10011.0}}, {"game_chats": {-10011, False}},
    {"game_bots": None}, {"game_bots": [99, True]}, {"game_bots": {99.0}},
])
def test_invalid_trust_configuration_does_not_supply_financial_authority(changes):
    assert admit(event(), **changes) is None


def test_conflicting_rejection_and_start_charge_cannot_justify_a_refund():
    result = parse_yinluo_resource_reply(
        ".召唤魔影", "你并非阴罗宗弟子。\n你消耗了 5000 点修为，召唤魔域的投影。",
    )
    assert result.phase == "conflict"
    assert result.required == (("summon_cost", "cultivation"),)


def test_forest_denial_does_not_erase_explicitly_reported_charge():
    result = parse_yinluo_resource_reply(
        ".血洗山林", "你消耗了 100 点煞气。\n生灵尚未恢复，煞气稀薄。",
    )
    assert result.phase == "conflict"
    assert values(result) == {("forest_sha", "sha", "actor"): -100}
    assert result.required == (("forest_sha", "sha"),)


def test_native_resolver_provides_the_original_command_provenance(monkeypatch):
    import asyncio
    import copy

    from model import app, state as state_module
    from model.verified_event import from_telegram_event

    monkeypatch.setattr(state_module, "_meta_state", copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.set_identity_account(1001, 11)
    original = SimpleNamespace(
        id=100, sender_id=1001, chat_id=-10011, raw_text=".化功为煞 10000",
        date=datetime.fromtimestamp(NOW - 10, timezone.utc), edit_date=None,
    )
    received = SimpleNamespace(
        id=110, sender_id=99, chat_id=-10011, date=datetime.fromtimestamp(NOW, timezone.utc),
        reply_to=SimpleNamespace(reply_to_msg_id=100), get_reply_message=AsyncMock(return_value=original),
    )
    _, context = asyncio.run(app._resolve_event_reply(received))
    source = from_telegram_event(received, event().text, context)
    owned = admit(source)
    assert owned is not None
    assert owned.command_msg_id == 100
    assert owned.command_at == NOW - 10
    assert owned.result_sender_id == 99
    original.edit_date = datetime.fromtimestamp(NOW - 5, timezone.utc)
    _, context = asyncio.run(app._resolve_event_reply(received))
    assert admit(from_telegram_event(received, event().text, context)) is None


def test_native_panel_does_not_clamp_a_server_overcap_balance():
    panel = parse_yinluo_resource_panel(sample("yinluo.banner.basic"))
    assert panel.sha == 269465
    assert panel.sha_max == 25000
    assert dict(panel.souls) == {"妖兽精魄": 191, "修士残魂": 134, "怨魂": 47, "凶兽戾魄": 1}
    assert panel.issues == ()


def test_sparse_panel_does_not_report_absent_soul_types_as_zero():
    panel = parse_yinluo_resource_panel(sample("yinluo.banner.sanshaoye_ready"))
    assert panel.sha == 100
    assert dict(panel.souls) == {"妖兽精魄": 4}
    assert "凶兽戾魄" not in dict(panel.souls)


@pytest.mark.parametrize("value", ["-1", "+1", "1.5", "1e3", "１０", "未知", str(MAX_BALANCE + 1)])
def test_invalid_panel_balances_are_not_zero_or_partial_numbers(value):
    panel = parse_yinluo_resource_panel(f"【道友的阴罗幡】\n煞气池: {value} / 100 (10%)")
    assert panel.sha is None
    assert panel.sha_max is None
    assert panel.issues == ("invalid_sha_panel",)


@pytest.mark.parametrize("text", [
    "【道友的阴罗幡】",
    "【道友的阴罗幡】\n幡魂总炼化: 10 缕",
    "【道友的阴罗幡】\n幡魂谱系:\n- 凶兽戾魄 · 灭法幡: 10 缕",
])
def test_missing_panel_financial_fields_stay_unknown(text):
    panel = parse_yinluo_resource_panel(text)
    assert panel.sha is panel.sha_max is panel.souls is None


@pytest.mark.parametrize("body", [
    "", "炼化槽:\n1号槽: [空闲]", "- 凶兽戾魄: 未知 缕",
    "- 凶兽戾魄: 1 缕\n- 凶兽戾魄: 2 缕",
    "- 凶兽戾魄: 1 缕\n- 怨魂: 未知 缕",
    "- 凶兽戾魄: 1 缕\n还有其他魂魄",
    "- 凶兽戾魄: 1 缕\n魂魄储备:\n- 怨魂: 1 缕",
])
def test_partial_or_conflicting_inventory_section_is_not_a_complete_snapshot(body):
    panel = parse_yinluo_resource_panel("【道友的阴罗幡】\n煞气池: 10 / 100 (10%)\n魂魄储备:\n" + body)
    assert panel.sha == 10
    assert panel.souls is None
    assert "incomplete_soul_panel" in panel.issues


def test_zero_is_retained_only_when_explicitly_reported():
    panel = parse_yinluo_resource_panel("【道友的阴罗幡】\n煞气池: 0 / 100 (0%)\n魂魄储备:\n- 怨魂: 0 缕")
    assert panel.sha == 0
    assert dict(panel.souls) == {"怨魂": 0}


@pytest.mark.parametrize("text", [None, "", "煞气池: 10 / 100 (10%)", "普通留言\n【道友的阴罗幡】", "【道友的阴罗幡】\n【另一个人的阴罗幡】"])
def test_nonpanel_or_combined_panels_are_not_financial_snapshots(text):
    assert parse_yinluo_resource_panel(text) is None


def assist_reply(command=".剥离咒源 @target_one", *, actor="provider_one", target="target_one", payer="provider_one"):
    source = admit(event(command))
    parsed = parse_yinluo_resource_reply(
        command,
        f"【剥离咒源失败】\n@{actor} 以阴罗幡镇咒，阴罗幡煞气被吞去 120 点，"
        f"@{payer} 修为折损 500，@{target} 魂封 +4。",
    )
    return source, parsed


def test_named_cost_is_charged_only_to_the_original_command_actor():
    source, reply = assist_reply()
    qualified = qualify_yinluo_resource_reply(source, reply, {1001: {"provider_one"}, 2002: {"target_one"}})
    assert qualified.phase == "failed"
    assert values(qualified) == {
        ("assist_sha", "sha", "actor"): -120,
        ("assist_backlash", "cultivation", "actor"): -500,
    }


def test_renamed_helper_and_target_can_use_unique_confirmed_aliases():
    source, reply = assist_reply(".剥离咒源 @target_old", actor="provider_old", payer="provider_old")
    qualified = qualify_yinluo_resource_reply(source, reply, {
        1001: {"@Provider_one", "provider_old"}, 2002: {"target_one", "target_old"},
    })
    assert qualified.phase == "failed"
    assert qualified.effects[1].amount == -500


@pytest.mark.parametrize("names", [
    {1001: {"provider_one"}, 2002: {"target_one", "provider_one"}},
    {1001: {"another_provider"}, 2002: {"target_one"}},
    {2002: {"target_one", "provider_one"}},
    {},
])
def test_ambiguous_or_unknown_actor_is_not_reassigned_to_a_different_account(names):
    source, reply = assist_reply()
    qualified = qualify_yinluo_resource_reply(source, reply, names)
    assert qualified.phase == "conflict"
    assert all(effect.amount is None and effect.subject == "actor" for effect in qualified.effects)
    assert "assist_actor_unverified" in qualified.issues


@pytest.mark.parametrize("names", [
    {1001: {"provider_one"}, 2002: {"target_one"}, 3003: {"target_other"}},
    {1001: {"provider_one"}, 2002: {"target_one", "target_other"}, 3003: {"target_other"}},
    {1001: {"provider_one"}},
])
def test_other_or_ambiguous_target_keeps_provider_cost_unknown(names):
    source, reply = assist_reply(target="target_other")
    qualified = qualify_yinluo_resource_reply(source, reply, names)
    assert qualified.phase == "conflict"
    assert all(effect.amount is None for effect in qualified.effects)
    assert "assist_target_mismatch" in qualified.issues


def test_beneficiary_cultivation_loss_cannot_be_assigned_to_the_provider():
    source, reply = assist_reply(payer="target_one")
    qualified = qualify_yinluo_resource_reply(source, reply, {1001: {"provider_one"}, 2002: {"target_one"}})
    assert qualified.phase == "conflict"
    assert all(effect.amount is None for effect in qualified.effects)
    assert "resource_payer_unverified" in qualified.issues


def test_second_person_reply_does_not_need_a_guessed_display_name():
    source = admit(event(".化功为煞 10000"))
    reply = parse_yinluo_resource_reply(source.command.text, event().text)
    qualified = qualify_yinluo_resource_reply(source, reply, {})
    assert qualified.phase == "success"
    assert qualified.effects[0].amount == -10000


@pytest.mark.parametrize("names", [None, {True: {"provider_one"}}, {1001: "provider_one"}, {1001: {"@@provider_one"}}, {1001: {None}}])
def test_invalid_alias_maps_do_not_become_ownership_evidence(names):
    source, reply = assist_reply()
    assert qualify_yinluo_resource_reply(source, reply, names) is None


@pytest.mark.parametrize("account_id", [0, -1, True, "11", None])
def test_missing_or_corrupt_account_binding_is_not_a_default_account(account_id):
    assert admit(event(), identity_accounts={1001: account_id}) is None
