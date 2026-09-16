import asyncio
import copy
from dataclasses import replace
from datetime import datetime
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model import action_guard, app
from model import cultivation_accounting as cultivation
from model import persistence, state as state_module
from model import runtime as transport
from model import yinluo_accounting as accounting
from model import yinluo_resource_book as books
from model.features import passive_inbox, wanxin, yinluo
from model.verified_event import VerifiedGameEvent
from model.yinluo_resource_replay import read_yinluo_log_batch
from test_cultivation_accounting import ACCOUNT, IDENTITY, env, snapshot  # noqa: F401
from yinluo_native_support import native_logs as logs_for


CHAT = -10065001
BOT = 65099
TARGET = IDENTITY + 1
CONVERT = "【转化成功】\n你成功将 10000 点修为炼化，煞气池增加了 2000 点！"


def event(command, text, *, root=100, msg_id=110, start=110, end=120, edited=False, chat=CHAT, identity=IDENTITY):
    return VerifiedGameEvent(
        event_type="edit" if edited else "message", chat_id=chat, msg_id=msg_id, sender_id=BOT, text=text,
        reply_context={"reply_to_command": command, "reply_to_msg_id": root, "root_msg_id": root,
                       "reply_to_sender_id": identity, "reply_to_server_at": start, "reply_to_command_edited": False,
                       "chat_id": chat},
        identity_id=identity, family="", root_msg_id=root, route_source="native", reply_to_sender_id=identity,
        server_event_at=end,
    )


def panel(*, sha=2000, msg_id=20, start=104, end=105, slots="1号槽: [空闲]\n2号槽: [魂力枯竭]", souls=5):
    return event(".我的阴罗幡", f"【道友的阴罗幡】\n煞气池: {sha} / 10000 (20%)\n"
                 f"魂魄储备:\n- 妖兽精魄: {souls} 缕\n炼化槽:\n{slots}", root=msg_id - 1, msg_id=msg_id, start=start, end=end)


def observe(received, *, now=None):
    return yinluo.observe_yinluo_resources(received, now=now or max(1000, received.server_event_at))


@pytest.fixture
def runtime(env, monkeypatch):  # noqa: F811
    state_module.set_game_group_id(CHAT)
    state_module.set_game_bot_ids([BOT])
    state_module._meta_state["global_enabled"] = True
    state_module.update_send_as_profile(IDENTITY, username="provider_one", enabled=True, sect_name="阴罗宗", realm="元婴后期")
    env["yinluo_enabled"] = True
    monkeypatch.setattr(yinluo.time, "time", lambda: 110.0)
    assert snapshot()
    assert observe(panel())
    return env


@pytest.fixture
def receipts(runtime, monkeypatch):
    for name in ("_append_sent_message_log", "_notify_game_command_sent_observers", "note_game_command_sent", "note_shadow_attempt_sent"):
        monkeypatch.setattr(transport, name, lambda *_args, **_kwargs: None)
    for name in ("_reply_chain_tracker", "_GAME_SEND_TASKS", "_GAME_SEND_BLOCK_LAST", "_SEND_AS_PEER_INVALID_UNTIL",
                 "_CHANNEL_SEND_AS_INVALID_UNTIL", "_CHANNEL_SEND_AS_INVALID_OBSERVATIONS", "_IDENTITY_LAST_SEND_AT", "_MODULE_LAST_SEND_AT"):
        monkeypatch.setattr(transport, name, {})
    monkeypatch.setattr(transport, "_GAME_LAST_SEND_AT", 0.0)
    monkeypatch.setattr(transport, "is_game_send_quiesced", lambda: False)
    monkeypatch.setattr(action_guard, "_recent_closed_command_guards", {})
    return runtime


def finalize(record, *, detached=False, root=100, at=110.5):
    return transport._finalize_game_send_receipt({
        "message": None, "detached": detached, "send_as_id": IDENTITY, "command": record["command"],
        "finalize_kwargs": {
            "send_as_id": IDENTITY, "game_group_id": CHAT, "topic_id": 0,
            "track": True, "max_retry": 0, "reply_timeout": 90, "send_started_at": record["started_at"],
            "send_intent": {"source_module": record["source_module"], "op_id": record["op_id"]},
        },
    }, msg_id=root, sent_at=at)


def prepare(command, now=110):
    record, reason = accounting.prepare_operation(IDENTITY, command, CHAT, now, source_module="阴罗宗")
    assert reason == ""
    return record


def bind(record, *, root=100, at=110):
    assert accounting.record_transport(IDENTITY, record["op_id"], phase="sent", msg_id=root, chat_id=CHAT, sent_at=at)


def value(resource="sha"):
    return accounting.resource_balance(IDENTITY, resource)


def test_native_conversions_reconcile_shared_cultivation_and_replayed_results(runtime):
    assert observe(event(".化功为煞 10000", CONVERT))
    assert observe(event(".化功为煞 10000", CONVERT, root=200, msg_id=210, start=130, end=140))
    before = copy.deepcopy(runtime)
    assert not observe(event(".化功为煞 10000", CONVERT))
    assert runtime == before
    assert value() == {"status": "ready", "value": 6000}
    assert cultivation.cultivation_balance(IDENTITY) == {"status": "ready", "value": 480000}
    assert state_module.get_send_as_profile(IDENTITY)["xiuwei_current"] == 480000


def test_reservation_is_not_a_debit_and_shared_duel_gate_sees_it(runtime):
    before = copy.deepcopy(runtime["yinluo_observation"])
    record = prepare(".化功为煞 10000")
    assert runtime["yinluo_observation"] == before
    assert state_module.get_send_as_profile(IDENTITY)["xiuwei_current"] == 500000
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000
    assert accounting.admission_reason(IDENTITY, ".化功为煞 10000", exclude=record["op_id"]) == ""
    assert accounting.admission_reason(IDENTITY, ".安抚幡灵 2") == "yinluo_command_in_flight"
    bind(record)
    assert observe(event(".化功为煞 10000", CONVERT))
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000


def test_refine_denial_never_rolls_back_interleaved_income(runtime):
    before = copy.deepcopy(runtime["yinluo_observation"])
    record = prepare(".囚禁魂魄 1 妖兽精魄")
    bind(record)
    assert value()["value"] == 1600
    assert runtime["yinluo_observation"] == before
    assert observe(event(".每日献祭", "你引动九幽煞气灌入幡中，煞气池增加了 500 点。", root=200, msg_id=210, start=112, end=115))
    assert value()["value"] == 2100
    assert observe(event(".囚禁魂魄 1 妖兽精魄", "魂魄袋中没有【妖兽精魄】", end=120))
    assert value()["value"] == 2500
    assert value("soul:妖兽精魄")["value"] == 5
    assert runtime["yinluo_observation"]["empty_slot_numbers"] == [1]


def test_refine_success_without_sha_cost_does_not_invent_400(runtime):
    record = prepare(".囚禁魂魄 1 妖兽精魄")
    bind(record)
    assert observe(event(".囚禁魂魄 1 妖兽精魄", "一缕【妖兽精魄】被强行打入1号炼化槽，炼化已开始。"))
    assert value()["value"] is None
    assert value("soul:妖兽精魄")["value"] == 4
    assert runtime["yinluo_observation"]["sha_current"] == 2000
    assert runtime["yinluo_observation"]["refining_slot_numbers"] == [1]
    assert observe(panel(sha=1600, msg_id=220, start=129, end=130, souls=4, slots="1号槽: [炼化中]"))
    assert value()["value"] == 1600


def test_summon_charge_then_backlash_is_two_components_of_one_operation(runtime):
    record = prepare(".召唤魔影")
    bind(record)
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None
    assert observe(event(".召唤魔影", "你消耗了 5000 点修为，召唤魔域的投影。"))
    assert observe(event(".召唤魔影", "召唤成功，镇压失败！修为暴跌了 1362 点！", edited=True, end=130))
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 493638
    assert not observe(event(".召唤魔影", "你消耗了 5000 点修为，召唤魔域的投影。"))


def test_actual_manual_send_retains_early_completion(runtime, monkeypatch):
    async def send(command, **kwargs):
        saved = runtime[accounting.STATE_KEY]["operations"][0]
        assert saved["phase"] == "prepared"
        assert kwargs["operation_check"]()
        assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000
        assert observe(event(command, CONVERT, end=111))
        return SimpleNamespace(id=100, chat_id=CHAT, sent_at=110.5)

    monkeypatch.setattr(yinluo, "send_game_command", send)
    ok, message, _plan = asyncio.run(yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=110))
    assert ok, message
    assert runtime[accounting.STATE_KEY]["operations"][0]["phase"] == "complete"
    assert runtime["yinluo_observation"]["last_result"] == "success"
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000


def test_cancelled_send_remains_reserved_after_sqlite_reload(runtime, monkeypatch):
    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(yinluo, "send_game_command", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=110))
    assert runtime[accounting.STATE_KEY]["operations"][0]["phase"] == "unknown"
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000
    spy = AsyncMock()
    monkeypatch.setattr(yinluo, "send_game_command", spy)
    asyncio.run(yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=120))
    spy.assert_not_awaited()


def test_confirmed_unsent_releases_but_stale_block_does_not(receipts, monkeypatch):
    async def unsent(command, **_options):
        transport._record_game_send_block(IDENTITY, command, "global_disabled", "paused", definitely_unsent=True)
        return None

    monkeypatch.setattr(yinluo, "send_game_command", unsent)
    asyncio.run(yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=110))
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 500000
    monkeypatch.setattr(yinluo, "send_game_command", AsyncMock(return_value=None))
    asyncio.run(yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=111))
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000


@pytest.mark.parametrize("change", ["disable", "rebind", "replace", "income", "config", "sect"])
def test_dispatch_revalidates_owned_state_and_does_not_write_to_replacements(runtime, monkeypatch, change):
    async def send(command, **kwargs):
        assert kwargs["operation_check"]()
        if change == "disable":
            runtime["yinluo_enabled"] = False
        elif change == "rebind":
            state_module.set_identity_account(IDENTITY, ACCOUNT + 1)
        elif change == "replace":
            state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(runtime)
        elif change == "income":
            assert observe(event(".每日献祭", "你引动九幽煞气灌入幡中，煞气池增加了 500 点。", root=300, msg_id=310, start=110, end=111))
        elif change == "sect":
            state_module.update_send_as_profile(IDENTITY, sect_name="天星宗")
        else:
            runtime["yinluo_observation"]["auto_config"]["convert_amount"] = 20000
        assert not kwargs["operation_check"]()
        return None

    monkeypatch.setattr(yinluo, "send_game_command", send)
    asyncio.run(yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=110))


def _deny_resources(operation, table, _column, *_args):
    return sqlite3.SQLITE_DENY if operation == sqlite3.SQLITE_INSERT and table == "identity_runtime_state" else sqlite3.SQLITE_OK


def test_prepare_persistence_failure_never_crosses_send_boundary(runtime, monkeypatch):
    spy = AsyncMock()
    connection = persistence.get_db_conn()
    before = copy.deepcopy(runtime[accounting.STATE_KEY])
    connection.set_authorizer(_deny_resources)
    try:
        msg, reason, record = asyncio.run(yinluo.send_owned_yinluo_command(
            ".化功为煞 10000", 110, send_as_id=IDENTITY, source_module="阴罗宗", operation_check=lambda: True, sender=spy,
        ))
    finally:
        connection.set_authorizer(None)
    assert (msg, reason, record) == (None, "persistence_failed", None)
    assert runtime[accounting.STATE_KEY] == before
    spy.assert_not_awaited()


def test_fact_and_reservation_commit_failure_rolls_back_memory_and_sqlite(runtime):
    record = prepare(".化功为煞 10000")
    bind(record)
    before = copy.deepcopy(runtime)
    connection = persistence.get_db_conn()
    connection.set_authorizer(_deny_resources)
    try:
        assert not observe(event(".化功为煞 10000", CONVERT))
    finally:
        connection.set_authorizer(None)
    assert runtime == before
    assert state_module.get_send_as_profile(IDENTITY)["xiuwei_current"] == 500000
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    restored = state_module.get_identity_state(IDENTITY)
    assert restored[accounting.STATE_KEY] == before[accounting.STATE_KEY]
    assert observe(event(".化功为煞 10000", CONVERT))
    assert value()["value"] == 4000
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000


def test_native_read_can_reconcile_money_but_cannot_complete_unknown_refine(runtime):
    record = prepare(".囚禁魂魄 1 妖兽精魄")
    bind(record)
    assert observe(panel(msg_id=220, start=129, end=130))
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "sent"
    assert value()["value"] == 1600
    assert accounting.admission_reason(IDENTITY, ".囚禁魂魄 1 妖兽精魄") == "yinluo_command_in_flight"


def test_blank_panel_edit_invalidates_its_balance_without_zeroing_stock(runtime):
    original = panel()
    assert observe(replace(original, event_type="edit", text="", server_event_at=109))
    assert value()["value"] is None
    assert value("soul:妖兽精魄")["value"] is None
    assert runtime["yinluo_observation"]["soul_stocks"]["妖兽精魄"] == 5
    assert observe(panel(msg_id=30, start=120, end=121))
    assert value()["value"] == 2000


def test_manual_financial_reply_is_accepted_after_module_disable(runtime):
    runtime["yinluo_enabled"] = False
    state_module._meta_state["global_enabled"] = False
    assert observe(event(".化功为煞 10000", CONVERT))
    assert value()["value"] == 4000
    assert not runtime["yinluo_enabled"]
    assert not state_module._meta_state["global_enabled"]


def test_context_free_text_and_wrong_native_actor_never_change_resources(runtime):
    before = copy.deepcopy(runtime)
    with state_module.use_identity(IDENTITY):
        assert not yinluo.apply_yinluo_passive(CONVERT, now=120)
        assert not yinluo.apply_yinluo_passive(CONVERT, now=120, event_context={"identity_id": IDENTITY})
    assert not observe(replace(event(".化功为煞 10000", CONVERT), sender_id=BOT + 1))
    assert runtime == before


def test_passive_inbox_uses_native_resource_evidence_before_text_dedupe(runtime):
    original = event(".化功为煞 10000", CONVERT)
    assert asyncio.run(passive_inbox.handle_passive_module_card(original, now=120))
    assert not asyncio.run(passive_inbox.handle_passive_module_card(original, now=121))
    edited = replace(original, event_type="edit", text=CONVERT.replace("2000", "1800"), server_event_at=125)
    assert asyncio.run(passive_inbox.handle_passive_module_card(edited, now=125))
    assert value()["value"] == 3800


def _target(runtime):
    state_module.set_identity_account(TARGET, ACCOUNT + 2)
    state_module.update_send_as_profile(TARGET, username="target_one", enabled=True)
    target = state_module.get_identity_state(TARGET)
    target["wanxin_enabled"] = True
    target["wanxin_observation"] = wanxin.normalize_wanxin_observation({
        "assist": {"send_as_id": IDENTITY},
        "commission": {"id": 10, "accepted": True, "published_at": 80, "accepted_at": 90, "helper_username": "provider_one"},
    })
    return target


def test_wanxin_strip_failure_debits_provider_and_consumes_only_target_commission(runtime):
    target = _target(runtime)
    text = "【剥离咒源失败】\n@provider_one 替 @target_one 剥离阴罗残咒，阴罗幡煞气被吞去 120 点。\n@provider_one 修为折损 500点"
    assert observe(event(".剥离咒源 @target_one", text))
    assert value()["value"] == 1880
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 499500
    assert target["wanxin_observation"]["commission"]["id"] == 0
    assert not observe(event(".剥离咒源 @target_one", text))


def test_wanxin_success_without_cost_keeps_sha_unknown_instead_of_charging_default(runtime):
    _target(runtime)
    text = "【剥离咒源成功】\n@provider_one 替 @target_one 剥下一段阴罗残咒。"
    assert observe(event(".剥离咒源 @target_one", text))
    assert value()["value"] is None
    assert runtime["yinluo_observation"]["sha_current"] == 2000


@pytest.mark.parametrize("corrupt", [None, [], {"book": {}}])
def test_corruption_is_preserved_across_sqlite_reload(runtime, corrupt):
    runtime[accounting.STATE_KEY] = corrupt
    assert accounting.admission_reason(IDENTITY, ".化功为煞 10000") == "corrupt_yinluo_accounting"
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert accounting.admission_reason(IDENTITY, ".化功为煞 10000") == "corrupt_yinluo_accounting"


def test_real_recovery_replays_original_command_and_final_edit_once(runtime):
    record = prepare(".召唤魔影")
    bind(record)
    rows = logs_for(
        event(".召唤魔影", "你消耗了 5000 点修为，召唤魔域的投影。"),
        event(".召唤魔影", "召唤成功，镇压失败！修为暴跌了 1362 点！", edited=True, end=130),
    )
    assert yinluo.recover_yinluo_resources(IDENTITY, 140, entries=rows)
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 493638
    assert not yinluo.recover_yinluo_resources(IDENTITY, 140, entries=list(reversed(rows)))


@pytest.mark.parametrize("missing", ["original", "native_clock", "wrong_chat", "bookkeeping_only"])
def test_recovery_cannot_invent_original_command_evidence(runtime, missing):
    record = prepare(".化功为煞 10000")
    bind(record)
    rows = logs_for(event(".化功为煞 10000", CONVERT))
    if missing == "original":
        rows.pop(0)
    elif missing == "native_clock":
        rows[0].pop("server_event_at")
        rows[0]["ts_epoch"] = 110
    elif missing == "wrong_chat":
        rows[0]["chat_id"] = CHAT - 1
    else:
        rows[0]["event_type"] = "sent"
    assert not yinluo.recover_yinluo_resources(IDENTITY, 140, entries=rows)
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "sent"
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000


def test_bounded_log_reader_keeps_native_evidence_and_ignores_invalid_rows(runtime, tmp_path):
    rows = logs_for(event(".化功为煞 10000", CONVERT))
    for row in rows:
        row["ts"] = datetime.fromtimestamp(row["server_event_at"], yinluo.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S")
    path = tmp_path / "1970-01-01.log"
    path.write_text("null\n[]\ninvalid\n" + "\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    assert read_yinluo_log_batch(140, since=110, messages_dir=tmp_path) == rows
    assert yinluo.recover_yinluo_resources(IDENTITY, 140, entries=read_yinluo_log_batch(140, since=110, messages_dir=tmp_path))
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000


def test_only_read_operations_can_expire_without_receipts(runtime):
    record = prepare(".我的阴罗幡")
    bind(record)
    assert not accounting.expire_unanswered_reads(IDENTITY, now=709, timeout=600)
    assert accounting.expire_unanswered_reads(IDENTITY, now=710, timeout=600)
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "read_expired"
    consuming = prepare(".化功为煞 10000", now=720)
    assert not accounting.expire_unanswered_reads(IDENTITY, now=2000, timeout=600)
    assert accounting.current_operation(IDENTITY, consuming["op_id"])["phase"] == "prepared"
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000


def test_wanxin_pending_recovery_uses_provider_and_exact_native_root(runtime, monkeypatch):
    target = _target(runtime)
    target["wanxin_observation"]["pending"] = {
        "action": "banner", "family": "wanxin_assist_banner", "send_as_id": IDENTITY,
        "msg_id": 100, "chat_id": CHAT, "sent_at": 110, "reply_due_at": 120,
    }
    text = "此咒契刚借幡镇魂过，阴煞尚未归位。借幡镇魂 冷却 6 小时，请在 5小时59分钟10秒 后再试。"
    rows = logs_for(event(".借幡镇魂 @target_one", text))
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: rows)
    with state_module.use_identity(TARGET):
        observed = wanxin.normalize_wanxin_observation(target["wanxin_observation"])
        assert asyncio.run(wanxin._recover_wanxin_pending_from_message_log(observed, 140))
    assert not observed["pending"]
    assert observed["assist"]["next_banner_time"] > 120 + 5 * 3600
    assert value()["value"] == 2000


def test_wanxin_unknown_recovery_deadline_does_not_slide_every_tick(runtime):
    observed = wanxin.normalize_wanxin_observation({
        "pending": {"action": "strip", "send_as_id": IDENTITY, "msg_id": 100,
                    "chat_id": CHAT, "sent_at": 110, "reply_due_at": 120},
    })
    assert wanxin._pending_blocks(observed, 121)
    due = observed["auto_next_time"]
    assert wanxin._pending_blocks(observed, 122)
    assert wanxin._pending_blocks(observed, due - 1)
    assert observed["auto_next_time"] == due


def _start_owned_wanxin_strip(runtime, monkeypatch):
    target = _target(runtime)
    target["wanxin_observation"]["assist"].update(identify_enabled=False, banner_enabled=False)
    target["wanxin_observation"].update(next_visit_time=1000, next_protect_time=1000, next_deduce_time=1000)
    monkeypatch.setattr(wanxin, "_iter_message_log_entries_between", lambda *_args: iter(()))
    monkeypatch.setattr(wanxin, "send_audit_log", AsyncMock())
    monkeypatch.setattr(wanxin, "send_game_command", AsyncMock(return_value=SimpleNamespace(id=100, chat_id=CHAT, sent_at=110)))
    with state_module.use_identity(TARGET):
        asyncio.run(wanxin.run_wanxin_scheduler(110))
    assert target["wanxin_observation"]["pending"]["action"] == "strip"
    return target


def test_held_wanxin_strip_retains_reservations_and_late_result_preserves_owner_work(runtime, monkeypatch):
    target = _start_owned_wanxin_strip(runtime, monkeypatch)
    op_id = target["wanxin_observation"]["pending"]["resource_op_id"]
    assert value()["value"] == 1880
    assert cultivation.cultivation_balance(IDENTITY)["value"] is None
    target["wanxin_observation"]["next_visit_time"] = 200
    monkeypatch.setattr(wanxin.time, "time", lambda: 300.0)
    monkeypatch.setattr(wanxin, "send_game_command", AsyncMock(return_value=SimpleNamespace(id=200, chat_id=CHAT, sent_at=300)))
    with state_module.use_identity(TARGET):
        asyncio.run(wanxin.run_wanxin_scheduler(300))
    observed = target["wanxin_observation"]
    assert observed["pending"]["action"] == "visit"
    assert observed["unresolved_actions"]["strip"]["resource_op_id"] == op_id
    assert value()["value"] == 1880
    assert accounting.current_operation(IDENTITY, op_id)["phase"] == "sent"
    active = copy.deepcopy(observed["pending"])
    result = event(".剥离咒源 @target_one", "【剥离咒源失败】\n@provider_one 替 @target_one 施术失败，"
                   "阴罗幡煞气被吞去 120 点。\n@provider_one 修为折损 500点")
    assert observe(result)
    observed = target["wanxin_observation"]
    assert observed["pending"] == active
    assert not observed["unresolved_actions"]
    assert observed["commission"]["id"] == 0
    assert accounting.current_operation(IDENTITY, op_id)["phase"] == "complete"
    assert value()["value"] == 1880
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 499500
    assert not observe(result)


def test_missing_accounting_book_cannot_hide_a_held_wanxin_reservation(runtime, monkeypatch):
    target = _start_owned_wanxin_strip(runtime, monkeypatch)
    observed = target["wanxin_observation"]
    with state_module.use_identity(TARGET):
        assert not wanxin._pending_blocks(observed, 300)
    assert not observed["pending"]
    assert "strip" in observed["unresolved_actions"]
    runtime[accounting.STATE_KEY] = {}
    assert accounting._legacy_pending(IDENTITY)
    assert accounting.admission_reason(IDENTITY, ".化功为煞 10000") == "legacy_pending"
    assert accounting.reserved_cultivation(IDENTITY)["status"] != "ready"


@pytest.mark.parametrize("new_commission", [False, True])
def test_duplicate_financial_result_clears_only_its_restored_owner_slot(runtime, monkeypatch, new_commission):
    target = _start_owned_wanxin_strip(runtime, monkeypatch)
    target["wanxin_observation"]["next_visit_time"] = 200
    monkeypatch.setattr(wanxin.time, "time", lambda: 300.0)
    monkeypatch.setattr(wanxin, "send_game_command", AsyncMock(return_value=SimpleNamespace(id=200, chat_id=CHAT, sent_at=300)))
    with state_module.use_identity(TARGET):
        asyncio.run(wanxin.run_wanxin_scheduler(300))
    held = copy.deepcopy(target["wanxin_observation"]["unresolved_actions"]["strip"])
    result = event(".剥离咒源 @target_one", "【剥离咒源失败】\n@provider_one 替 @target_one 施术失败，"
                   "阴罗幡煞气被吞去 120 点。\n@provider_one 修为折损 500点")
    assert observe(result)
    if new_commission:
        target["wanxin_observation"]["commission"].update(id=11, published_at=330, accepted_at=340, accepted=True)
    expected = copy.deepcopy(target["wanxin_observation"])
    provider = copy.deepcopy(runtime)
    target["wanxin_observation"]["unresolved_actions"]["strip"] = held
    assert observe(result)
    assert target["wanxin_observation"] == expected
    assert runtime == provider
    assert value()["value"] == 1880
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 499500


@pytest.mark.parametrize("field", ["op_id", "account_id", "owner_account_id", "commission_id", "commission_published_at", "msg_id", "chat_id"])
def test_duplicate_financial_result_cannot_clear_a_conflicting_owner_binding(runtime, monkeypatch, field):
    target = _start_owned_wanxin_strip(runtime, monkeypatch)
    original = copy.deepcopy(target["wanxin_observation"]["pending"])
    result = event(".剥离咒源 @target_one", "【剥离咒源失败】\n@provider_one 替 @target_one 施术失败，"
                   "阴罗幡煞气被吞去 120 点。\n@provider_one 修为折损 500点")
    assert observe(result)
    original["status"] = "unknown"
    original[field] = "different-operation" if field == "op_id" else original[field] + 1
    target["wanxin_observation"]["unresolved_actions"]["strip"] = original
    before = copy.deepcopy(target["wanxin_observation"])
    assert not observe(result)
    assert target["wanxin_observation"] == before


@pytest.mark.parametrize("action", [[], {}, ["strip"]])
def test_malformed_legacy_owner_action_holds_provider_admission_without_crashing(runtime, action):
    target = _target(runtime)
    target["wanxin_observation"]["pending"] = {"send_as_id": IDENTITY, "action": action}
    runtime[accounting.STATE_KEY] = {}
    before = copy.deepcopy(target["wanxin_observation"])
    assert accounting.admission_reason(IDENTITY, ".化功为煞 10000") == "legacy_pending"
    assert target["wanxin_observation"] == before


def test_corrupt_owner_pending_cannot_be_cleared_by_a_financial_result(runtime):
    target = _target(runtime)
    target["wanxin_observation"]["unresolved_actions"] = ["damaged"]
    before = copy.deepcopy(target["wanxin_observation"])
    result = event(".剥离咒源 @target_one", "【剥离咒源失败】\n@provider_one 替 @target_one 施术失败，"
                   "阴罗幡煞气被吞去 120 点。\n@provider_one 修为折损 500点")
    assert observe(result)
    assert target["wanxin_observation"] == before
    assert value()["value"] == 1880


@pytest.mark.parametrize("change", ["account", "identity"])
def test_wanxin_assist_receipt_cannot_rebind_provider_after_await(runtime, monkeypatch, change):
    target = _target(runtime)
    observed = target["wanxin_observation"]
    observed["assist"].update(identify_enabled=False, banner_enabled=False)
    observed["next_visit_time"] = 1000

    async def send(_command, **kwargs):
        assert kwargs["operation_check"]()
        if change == "account":
            state_module.set_identity_account(IDENTITY, ACCOUNT + 10)
        else:
            state_module._meta_state["identity_states"][IDENTITY] = copy.deepcopy(runtime)
        assert not kwargs["operation_check"]()
        return SimpleNamespace(id=100, chat_id=CHAT, sent_at=110)

    monkeypatch.setattr(wanxin, "send_game_command", send)
    with state_module.use_identity(TARGET):
        asyncio.run(wanxin.run_wanxin_scheduler(110))
    pending = target["wanxin_observation"]["pending"]
    assert pending["account_id"] == ACCOUNT
    assert pending["send_as_id"] == IDENTITY
    assert pending["msg_id"] == 0
    assert pending["status"] == "sending"


def test_wanxin_resource_send_reserves_owner_before_await_and_blocks_reentry(runtime, monkeypatch):
    target = _target(runtime)
    target["wanxin_observation"]["assist"].update(identify_enabled=False, banner_enabled=False)
    seen = []

    async def send(command, **kwargs):
        seen.append((command, copy.deepcopy(target["wanxin_observation"]["pending"]), kwargs["op_id"]))
        with state_module.use_identity(TARGET):
            await wanxin.run_wanxin_scheduler(310)
        return SimpleNamespace(id=100, chat_id=CHAT, sent_at=110)

    monkeypatch.setattr(wanxin, "send_game_command", send)
    monkeypatch.setattr(wanxin, "_iter_message_log_entries_between", lambda *_args: iter(()))
    with state_module.use_identity(TARGET):
        asyncio.run(wanxin.run_wanxin_scheduler(110))
    assert len(seen) == 1
    assert seen[0][1].get("resource_op_id") == seen[0][2]
    assert seen[0][1]["status"] == "sending"
    assert target["wanxin_observation"]["pending"]["status"] == "sent"


def test_cancelled_wanxin_resource_send_keeps_owner_and_provider_intent(runtime, monkeypatch):
    target = _target(runtime)
    target["wanxin_observation"]["assist"].update(identify_enabled=False, banner_enabled=False)

    async def send(_command, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(wanxin, "send_game_command", send)
    with state_module.use_identity(TARGET), pytest.raises(asyncio.CancelledError):
        asyncio.run(wanxin.run_wanxin_scheduler(110))
    pending = target["wanxin_observation"]["pending"]
    assert pending.get("status") == "unknown"
    assert pending["resource_op_id"] == runtime[accounting.STATE_KEY]["operations"][0]["op_id"]
    assert runtime[accounting.STATE_KEY]["operations"][0]["phase"] == "unknown"
    assert value()["value"] == 1880


def test_wanxin_resource_owner_intent_save_failure_cannot_send_or_reserve_twice(runtime, monkeypatch):
    target = _target(runtime)
    target["wanxin_observation"]["assist"].update(identify_enabled=False, banner_enabled=False)
    sender = AsyncMock()
    monkeypatch.setattr(wanxin, "send_game_command", sender)
    monkeypatch.setattr(wanxin, "save_state", lambda: False)
    with state_module.use_identity(TARGET):
        asyncio.run(wanxin.run_wanxin_scheduler(110))
    sender.assert_not_awaited()
    assert not target["wanxin_observation"]["pending"]
    assert runtime[accounting.STATE_KEY]["operations"][0]["phase"] == "unsent"
    assert value()["value"] == 2000
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 500000


def test_wanxin_resource_early_reply_after_disable_is_not_overwritten(runtime, monkeypatch):
    target = _target(runtime)
    target["wanxin_observation"]["assist"].update(identify_enabled=False, banner_enabled=False)
    monkeypatch.setattr(wanxin, "send_audit_log", AsyncMock())
    early = []

    async def send(_command, **_kwargs):
        target["wanxin_enabled"] = False
        result = event(".剥离咒源 @target_one", "【剥离咒源失败】\n@provider_one 替 @target_one 施术失败，"
                       "阴罗幡煞气被吞去 120 点。\n@provider_one 修为折损 500点")
        assert observe(result)
        early.append(result)
        assert target["wanxin_observation"]["pending"]["status"] == "sending"
        assert target["wanxin_observation"]["commission"]["id"] == 10
        return SimpleNamespace(id=100, chat_id=CHAT, sent_at=110)

    monkeypatch.setattr(wanxin, "send_game_command", send)
    with state_module.use_identity(TARGET):
        asyncio.run(wanxin.run_wanxin_scheduler(110))
    assert not target["wanxin_enabled"]
    assert runtime[accounting.STATE_KEY]["operations"][0]["phase"] == "complete"
    assert target["wanxin_observation"]["pending"]["status"] == "sent"
    rows = logs_for(early[0])
    monkeypatch.setattr(wanxin, "_iter_message_log_entries_between", lambda *_args: iter((row, 310) for row in rows))
    with state_module.use_identity(TARGET):
        assert asyncio.run(wanxin._cleanup_wanxin_pending_only(310))
    assert not target["wanxin_observation"]["pending"]
    assert not target["wanxin_observation"]["unresolved_actions"]
    assert target["wanxin_observation"]["commission"]["id"] == 0
    assert runtime[accounting.STATE_KEY]["operations"][0]["phase"] == "complete"
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 499500


def test_wanxin_transaction_failure_rolls_back_both_identities(runtime):
    target = _target(runtime)
    assert persistence.save_state()
    before_provider, before_target = copy.deepcopy(runtime), copy.deepcopy(target)
    connection = persistence.get_db_conn()
    connection.execute(f"CREATE TEMP TRIGGER fail_target BEFORE INSERT ON identity_runtime_state "
                       f"WHEN NEW.send_as_id = {TARGET} BEGIN SELECT RAISE(ABORT, 'target test failure'); END")
    received = event(".剥离咒源 @target_one", "【剥离咒源失败】\n@provider_one 替 @target_one 施术失败，"
                     "阴罗幡煞气被吞去 120 点。\n@provider_one 修为折损 500点")
    try:
        assert not observe(received)
    finally:
        connection.execute("DROP TRIGGER fail_target")
    assert runtime == before_provider
    assert target == before_target
    assert state_module.get_send_as_profile(IDENTITY)["xiuwei_current"] == 500000
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    assert state_module.get_identity_state(TARGET)["wanxin_observation"] == before_target["wanxin_observation"]
    assert observe(received)


@pytest.mark.parametrize("change", ["account", "commission", "helper"])
def test_old_assist_receipt_charges_provider_without_clearing_replacement_commission(runtime, change):
    target = _target(runtime)
    record, reason = accounting.prepare_operation(IDENTITY, ".剥离咒源 @target_one", CHAT, 110, source_module="婉心封魂", beneficiary={
        "identity_id": TARGET, "account_id": ACCOUNT + 2, "commission_id": 10, "published_at": 80, "accepted_at": 90,
    })
    assert not reason
    bind(record)
    if change == "account":
        state_module.set_identity_account(TARGET, ACCOUNT + 3)
    elif change == "commission":
        target["wanxin_observation"]["commission"]["id"] = 11
    else:
        target["wanxin_observation"]["assist"]["send_as_id"] = IDENTITY + 9
    before = copy.deepcopy(target["wanxin_observation"])
    assert observe(event(".剥离咒源 @target_one", "【剥离咒源成功】\n@provider_one 替 @target_one 剥下一段阴罗残咒。幡面煞气被削去 120 点。"))
    assert target["wanxin_observation"] == before
    assert value()["value"] == 1880
    followup = prepare(".我的阴罗幡", now=125)
    assert accounting.record_transport(IDENTITY, followup["op_id"], phase="unsent")
    assert accounting.current_operation(IDENTITY, record["op_id"])["beneficiary"]["account_id"] == ACCOUNT + 2
    assert not observe(event(".剥离咒源 @target_one", "【剥离咒源成功】\n@provider_one 替 @target_one 剥下一段阴罗残咒。幡面煞气被削去 120 点。"))
    assert target["wanxin_observation"] == before


@pytest.mark.parametrize("text", [
    "【剥离咒源成功】\n@provider_one 替 @target_one 剥下一段阴罗残咒。幡面煞气被削去 120 点。",
    "你与对方没有有效的咒契协定，请对方先发布委托，再由你接取。",
])
def test_assist_projection_uses_explicit_beneficiary_not_active_context(runtime, text):
    target = _target(runtime)
    with state_module.use_identity(IDENTITY):
        assert observe(event(".剥离咒源 @target_one", text))
    assert target["wanxin_observation"]["commission"]["owner_username"] == "target_one"
    assert target["wanxin_observation"]["commission"]["id"] == 0
    assert runtime["wanxin_observation"] == {}


@pytest.mark.parametrize("text", [
    "你与对方没有有效的咒契协定，请对方先发布委托，再由你接取。",
    "此咒契刚借幡镇魂过，阴煞尚未归位。借幡镇魂 冷却 6 小时，请在 5小时59分钟10秒 后再试。",
])
@pytest.mark.parametrize("native", [False, True])
def test_assist_denial_without_family_cannot_bypass_native_contract(runtime, text, native):
    target = _target(runtime)
    target["wanxin_observation"]["pending"] = {
        "action": "banner", "family": "wanxin_assist_banner", "send_as_id": IDENTITY,
        "msg_id": 100, "chat_id": CHAT, "sent_at": 110, "reply_due_at": 120,
    }
    before = copy.deepcopy(target["wanxin_observation"])
    received = event(".借幡镇魂 @target_one", text)
    with state_module.use_identity(IDENTITY):
        handled = asyncio.run(wanxin.handle_wanxin_reply(
            text, 140, reply_to=SimpleNamespace(id=100), event=received if native else None,
        ))
    assert handled is native
    if native:
        assert not target["wanxin_observation"]["pending"]
    else:
        assert target["wanxin_observation"] == before
    assert value()["value"] == 2000


@pytest.mark.parametrize("mutation", [
    "too_many_points", "bad_points", "prepared_with_receipt", "unsent_with_receipt", "string_start", "bad_budget",
])
def test_staged_corruption_never_writes_then_poison_reads(runtime, monkeypatch, mutation):
    record = prepare(".化功为煞 10000")
    update = accounting.stage_event(event(".化功为煞 10000", CONVERT), now=140)
    item = update.value["operations"][0]
    if mutation == "too_many_points":
        update.value["business"] = {str(index): {"start": update.source.start, "end": update.source.end}
                                    for index in range(accounting.MAX_BUSINESS_POINTS + 1)}
    elif mutation == "bad_points":
        update.value["business"]["bad"] = {"start": update.source.end, "end": update.source.start}
    elif mutation in {"prepared_with_receipt", "unsent_with_receipt"}:
        item.update(phase=mutation.split("_")[0], msg_id=100, sent_at=110)
    elif mutation == "string_start":
        item["started_at"] = "110"
    else:
        item["budget"]["costs"]["cultivation"] = 0
    before = copy.deepcopy(runtime)
    save = SimpleNamespace(called=False)

    def cannot_save():
        save.called = True
        raise AssertionError("Invalid staged accounting reached persistence")

    monkeypatch.setattr(persistence, "save_state", cannot_save)
    assert not accounting.commit_update(update)
    assert not save.called
    assert runtime == before
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "prepared"


def test_business_capacity_is_a_bounded_hold_not_corrupt_state(runtime):
    points = copy.deepcopy(next(iter(runtime[accounting.STATE_KEY]["business"].values())))
    runtime[accounting.STATE_KEY]["business"] = {
        str(index): copy.deepcopy(points) for index in range(accounting.MAX_BUSINESS_POINTS)
    }
    update = accounting.stage_event(event(".化功为煞 10000", CONVERT), now=140)
    assert not yinluo._accept_business_point(update, "new_key")
    assert update.value["hold"] == "capacity"
    assert len(update.value["business"]) == accounting.MAX_BUSINESS_POINTS
    assert accounting.commit_update(update)
    stored, reason = accounting.read_accounting(IDENTITY)
    assert not reason
    assert stored["hold"] == "capacity"
    assert accounting.admission_reason(IDENTITY, ".化功为煞 10000") == "capacity"


def test_detached_runtime_receipt_can_recover_after_sqlite_reload(receipts, monkeypatch):
    async def cancelled(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(yinluo, "send_game_command", cancelled)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(yinluo.execute_yinluo_manual_action("convert", "10000", send_as_id=IDENTITY, now=110))
    record = receipts[accounting.STATE_KEY]["operations"][0]
    assert record["phase"] == "unknown"

    async def complete_rpc():
        ready, release = asyncio.Event(), asyncio.Event()

        async def rpc():
            ready.set()
            await release.wait()
            return SimpleNamespace(id=100)

        task, receipt = transport._start_game_send_rpc(
            rpc, account_id=ACCOUNT, command=record["command"], send_as_id=IDENTITY,
            game_group_id=CHAT, topic_id=0, track=True, max_retry=0, reply_timeout=90,
            send_intent={"source_module": record["source_module"], "op_id": record["op_id"]},
        )
        try:
            await ready.wait()
            transport._detach_game_send_rpc(task, receipt)
        finally:
            release.set()
            await task
            await asyncio.sleep(0)
        assert receipt["message"].id == 100

    asyncio.run(complete_rpc())
    pending = receipts["pending_tasks"][(CHAT, 100)]
    assert pending["send_caller_detached"] and pending["max_retry"] == 0
    assert "account_id" not in pending
    assert persistence.save_state()
    state_module._meta_state["identity_states"] = {}
    assert persistence.load_state()
    restored = state_module.get_identity_state(IDENTITY)
    assert accounting.adopt_pending_receipt(IDENTITY, record["op_id"])
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "sent"
    assert yinluo.recover_yinluo_resources(IDENTITY, 140, entries=logs_for(event(".化功为煞 10000", CONVERT)))
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert (CHAT, 100) not in restored["pending_tasks"]
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000
    assert not yinluo.recover_yinluo_resources(IDENTITY, 140, entries=logs_for(event(".化功为煞 10000", CONVERT)))
    assert not transport._GAME_SEND_TASKS


@pytest.mark.parametrize("mismatch", ["account", "op_id", "command", "module", "chat"])
def test_detached_receipt_requires_exact_owned_operation(receipts, mismatch):
    record = prepare(".化功为煞 10000")
    finalize(record, detached=True)
    pending = receipts["pending_tasks"][(CHAT, 100)]
    if mismatch == "account":
        pending["account_id"] = ACCOUNT + 1
    elif mismatch == "op_id":
        pending["op_id"] = "f" * 32
    elif mismatch == "command":
        pending["cmd"] = ".化功为煞 20000"
    elif mismatch == "module":
        pending["source_module"] = "婉心封魂"
    else:
        pending["chat_id"] = CHAT - 1
        receipts["pending_tasks"][(CHAT - 1, 100)] = receipts["pending_tasks"].pop((CHAT, 100))
    assert not accounting.adopt_pending_receipt(IDENTITY, record["op_id"])
    assert accounting.current_operation(IDENTITY, record["op_id"])["msg_id"] == 0


def test_wanxin_early_reply_and_real_finalize_do_not_restore_pending(receipts, monkeypatch):
    target = _target(receipts)
    text = "【剥离咒源成功】\n@provider_one 替 @target_one 剥下一段阴罗残咒。幡面煞气被削去 120 点。"
    early = []

    async def send(command, **kwargs):
        record = receipts[accounting.STATE_KEY]["operations"][0]
        assert kwargs["operation_check"]()
        assert record["beneficiary"]["identity_id"] == TARGET
        with state_module.use_identity(IDENTITY):
            early.append(event(command, text, end=111))
            assert observe(early[0])
        assert target["wanxin_observation"]["pending"]["status"] == "sending"
        assert target["wanxin_observation"]["commission"]["id"] == 10
        return finalize(record)

    monkeypatch.setattr(wanxin, "send_game_command", send)
    with state_module.use_identity(TARGET):
        observed = wanxin.normalize_wanxin_observation(target["wanxin_observation"])
        assert asyncio.run(wanxin._send_assist_action(observed, "strip", 110))
    assert observed["pending"]["status"] == "sent"
    assert observe(early[0])
    observed = target["wanxin_observation"]
    assert not observed["pending"]
    assert observed["commission"]["id"] == 0
    assert observed["commission"]["owner_username"] == "target_one"
    assert receipts[accounting.STATE_KEY]["operations"][0]["phase"] == "complete"
    assert (CHAT, 100) not in receipts["pending_tasks"]
    assert value()["value"] == 1880


@pytest.mark.parametrize("newer_guard", [False, True])
def test_manual_native_completion_closes_only_its_exact_pending_and_guard(receipts, newer_guard):
    command = ".化功为煞 10000"
    dummy = {"op_id": "manual", "source_module": "阴罗宗", "command": command, "started_at": 110}
    finalize(dummy)
    receipts["pending_tasks"][(CHAT - 1, 100)] = {"cmd": command, "chat_id": CHAT - 1}
    if newer_guard:
        finalize(dummy, root=200, at=115)
    key = action_guard.resolve_action_key(command)
    assert observe(event(command, "结算文案尚未提供"))
    assert (CHAT, 100) in receipts["pending_tasks"]
    assert key in receipts["action_guard_sessions"]
    assert yinluo.handle_yinluo_resource_reply(event(command, CONVERT, edited=True, end=130), now=140)
    assert (CHAT, 100) not in receipts["pending_tasks"]
    assert (CHAT - 1, 100) in receipts["pending_tasks"]
    assert (key in receipts["action_guard_sessions"]) is newer_guard
    if newer_guard:
        assert (CHAT, 200) in receipts["pending_tasks"]


def test_routed_native_completion_is_handled_after_early_observer(receipts, monkeypatch):
    command = ".化功为煞 10000"
    finalize({"op_id": "manual", "source_module": "阴罗宗", "command": command, "started_at": 110})
    received = event(command, CONVERT)
    assert observe(received)
    routed = SimpleNamespace(id=received.msg_id, chat_id=CHAT, sender_id=BOT, server_event_at=120)
    context = dict(received.reply_context, send_as_id=IDENTITY, family="yinluo_convert")
    monkeypatch.setattr(app, "_remember_early_routed_reply", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "record_unhandled_routed_reply", lambda *_args: pytest.fail("Known receipt was classified unhandled"))
    assert asyncio.run(app._handle_routed_reply_event(routed, CONVERT, 140, SimpleNamespace(id=100), context))
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000
    finalize({"op_id": "manual2", "source_module": "阴罗宗", "command": command, "started_at": 125}, root=200, at=125)
    before_guard = copy.deepcopy(receipts["action_guard_sessions"])
    routed.server_event_at = 130
    assert not asyncio.run(app._handle_routed_reply_event(
        routed, "结算文案暂时不可用", 140, SimpleNamespace(id=100), context, event_kind="edit",
    ))
    assert receipts["action_guard_sessions"] == before_guard
    assert (CHAT, 200) in receipts["pending_tasks"]


@pytest.mark.parametrize("bot_seen", [0, 1200])
@pytest.mark.parametrize("command", [".化功为煞 10000", ".剥离咒源 @target_one"])
def test_shared_retry_retains_unresolved_financial_work_without_generic_recovery(receipts, monkeypatch, bot_seen, command):
    record = prepare(command)
    finalize(record)
    bind(record)
    read_log = AsyncMock()
    rows = []
    monkeypatch.setattr(yinluo, "read_yinluo_log_batch", lambda *_args, **_kwargs: rows)
    monkeypatch.setattr(transport, "_recover_pending_reply_from_message_log", read_log)
    monkeypatch.setattr(transport, "should_pause_for_bot_health", lambda: False)
    monkeypatch.setattr(transport, "get_bot_last_seen_at", lambda: bot_seen)
    sender = AsyncMock(side_effect=AssertionError("Resource commands cannot use generic retries"))
    monkeypatch.setattr(transport, "send_game_command", sender)
    guard = copy.deepcopy(receipts["action_guard_sessions"])
    asyncio.run(transport.run_retry_scheduler(1200, send_as_id=IDENTITY))
    pending = receipts["pending_tasks"][(CHAT, 100)]
    assert pending["reply_recovery_error"] == "yinluo_resource_reply_unresolved"
    assert pending["reply_recovery_retry_at"] == 1800
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "sent"
    assert receipts["action_guard_sessions"] == guard
    asyncio.run(transport.run_retry_scheduler(1201, send_as_id=IDENTITY))
    assert pending["reply_recovery_retry_at"] == 1800
    sender.assert_not_awaited()
    read_log.assert_not_awaited()


def test_unstored_receipt_at_capacity_cannot_advance_assist_business(runtime, monkeypatch):
    target = _target(runtime)
    before = copy.deepcopy(target["wanxin_observation"])
    points = copy.deepcopy(runtime[accounting.STATE_KEY]["business"])
    monkeypatch.setattr(books, "MAX_RECEIPTS", 1)
    received = event(".剥离咒源 @target_one", "【剥离咒源成功】\n@provider_one 替 @target_one 剥下一段阴罗残咒。幡面煞气被削去 120 点。")
    assert observe(received)
    assert target["wanxin_observation"] == before
    assert runtime[accounting.STATE_KEY]["business"] == points
    assert runtime[accounting.STATE_KEY]["book"]["gap"]["reason"] == "capacity"
    assert not accounting.reply_complete(received, now=140)


@pytest.mark.parametrize("foreign", ["message", "sender", "chat"])
def test_unrelated_edits_do_not_rebuild_every_identity_resource_book(runtime, monkeypatch, foreign):
    received = replace(event(".化功为煞 10000", "unrelated", edited=True), reply_context=None, root_msg_id=0)
    if foreign == "sender":
        received = replace(received, sender_id=BOT + 1)
    elif foreign == "chat":
        received = replace(received, chat_id=CHAT - 1)
    monkeypatch.setattr(accounting, "read_accounting", lambda *_args: pytest.fail("Unrelated edit rebuilt accounting"))
    assert not observe(received)


@pytest.mark.parametrize("charge_edit", ["你消耗了 6000 点修为，召唤魔域的投影。", ""])
def test_later_charge_edit_does_not_reopen_confirmed_summon_outcome(runtime, charge_edit):
    record = prepare(".召唤魔影")
    bind(record)
    assert observe(event(".召唤魔影", "你消耗了 5000 点修为，召唤魔域的投影。"))
    assert observe(event(".召唤魔影", "召唤成功，镇压失败！修为暴跌了 1362 点！", msg_id=120, end=125))
    assert runtime["yinluo_observation"]["last_result"] == "failed"
    assert observe(event(".召唤魔影", charge_edit, edited=True, end=130))
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert runtime["yinluo_observation"]["last_result"] == "failed"
    balance = cultivation.cultivation_balance(IDENTITY)
    assert balance["value"] == (492638 if charge_edit else None)


def test_unknown_edit_of_final_result_reopens_only_its_owned_operation(runtime):
    record = prepare(".化功为煞 10000")
    bind(record)
    assert observe(event(".化功为煞 10000", CONVERT))
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert observe(event(".化功为煞 10000", "", edited=True, end=130))
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "unknown"
    assert accounting.admission_reason(IDENTITY, ".化功为煞 10000") == "yinluo_command_in_flight"


def test_abandoned_wanxin_resource_send_keeps_reservation_and_resolves_its_late_result(runtime, monkeypatch):
    class AbandonedResourceSend(BaseException):
        pass

    target = _target(runtime)
    observed = target["wanxin_observation"]
    observed["assist"].update(identify_enabled=False, banner_enabled=False)
    observed.update(next_visit_time=300, next_protect_time=1000, next_deduce_time=1000)
    monkeypatch.setattr(wanxin, "_iter_message_log_entries_between", lambda *_args: iter(()))
    monkeypatch.setattr(wanxin, "send_audit_log", AsyncMock())

    async def stop(_command, **_kwargs):
        assert target["wanxin_observation"]["pending"]["status"] == "sending"
        raise AbandonedResourceSend

    monkeypatch.setattr(wanxin, "send_game_command", stop)
    with state_module.use_identity(TARGET), pytest.raises(AbandonedResourceSend):
        asyncio.run(wanxin.run_wanxin_scheduler(110))
    original = copy.deepcopy(target["wanxin_observation"]["pending"])
    op_id = original["resource_op_id"]
    provider = copy.deepcopy(accounting.current_operation(IDENTITY, op_id))
    balances = value(), cultivation.cultivation_balance(IDENTITY)
    assert not wanxin._WANXIN_INFLIGHT
    monkeypatch.setattr(wanxin.time, "time", lambda: 300.0)
    sender = AsyncMock(return_value=SimpleNamespace(id=200, chat_id=CHAT, sent_at=300))
    monkeypatch.setattr(wanxin, "send_game_command", sender)
    with state_module.use_identity(TARGET):
        asyncio.run(wanxin.run_wanxin_scheduler(300))
    sender.assert_awaited_once_with(
        ".探望南宫婉", track=True, max_retry=0, reply_timeout=wanxin.WANXIN_REPLY_TIMEOUT_SEC,
        send_as_id=TARGET, source_module=wanxin.WANXIN_MODULE_NAME,
        op_id=target["wanxin_observation"]["pending"]["op_id"], target_chat_id=CHAT,
        queue_timeout=wanxin.WANXIN_SEND_QUEUE_TIMEOUT_SEC,
        operation_check=sender.await_args.kwargs["operation_check"],
    )
    observed = target["wanxin_observation"]
    active = copy.deepcopy(observed["pending"])
    assert observed["unresolved_actions"]["strip"] == dict(original, status="unknown")
    assert accounting.current_operation(IDENTITY, op_id) == provider
    assert (value(), cultivation.cultivation_balance(IDENTITY)) == balances
    assert observed["commission"]["id"] == 10
    result = event(".剥离咒源 @target_one", "【剥离咒源失败】\n@provider_one 替 @target_one 施术失败，"
                   "阴罗幡煞气被吞去 120 点。\n@provider_one 修为折损 500点")
    assert observe(result)
    observed = target["wanxin_observation"]
    assert observed["unresolved_actions"]["strip"] == dict(original, status="unknown")
    assert observed["commission"]["id"] == 10
    assert accounting.current_operation(IDENTITY, op_id) == provider
    bind(provider)
    assert observe(result)
    observed = target["wanxin_observation"]
    assert observed["pending"] == active
    assert not observed["unresolved_actions"]
    assert observed["commission"]["id"] == 0
    assert accounting.current_operation(IDENTITY, op_id)["phase"] == "complete"
    assert value()["value"] == 1880
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 499500
    assert not observe(result)


def test_active_financial_send_is_not_reclassified_by_paused_cleanup(runtime, monkeypatch):
    target = _target(runtime)
    target["wanxin_observation"]["assist"].update(identify_enabled=False, banner_enabled=False)
    monkeypatch.setattr(wanxin, "_iter_message_log_entries_between", lambda *_args: iter(()))

    async def send(_command, **options):
        original = copy.deepcopy(target["wanxin_observation"]["pending"])
        assert wanxin._WANXIN_INFLIGHT[TARGET, "strip"] == options["op_id"]
        target["wanxin_enabled"] = False
        with state_module.use_identity(TARGET):
            await wanxin._cleanup_wanxin_pending_only(310)
        assert target["wanxin_observation"]["pending"] == original
        assert not target["wanxin_observation"]["unresolved_actions"]
        return SimpleNamespace(id=100, chat_id=CHAT, sent_at=110)

    monkeypatch.setattr(wanxin, "send_game_command", send)
    with state_module.use_identity(TARGET):
        asyncio.run(wanxin.run_wanxin_scheduler(110))
    assert not wanxin._WANXIN_INFLIGHT
    assert not target["wanxin_enabled"]
    assert target["wanxin_observation"]["pending"]["status"] == "sent"


@pytest.mark.parametrize("recover_during_unwind", [False, True])
def test_native_detached_result_binds_receipt_before_closing_owner_and_provider(receipts, monkeypatch, recover_during_unwind):
    target = _target(receipts)
    target["wanxin_observation"]["assist"].update(identify_enabled=False, banner_enabled=False)
    real_adopt = accounting.adopt_pending_receipt
    deferred = []

    def adopt(identity_id, op_id):
        if not recover_during_unwind and not deferred:
            deferred.append(op_id)
            return False
        return real_adopt(identity_id, op_id)

    monkeypatch.setattr(accounting, "adopt_pending_receipt", adopt)

    async def send(_command, **_options):
        finalize(receipts[accounting.STATE_KEY]["operations"][0], detached=True)
        raise asyncio.CancelledError

    monkeypatch.setattr(wanxin, "send_game_command", send)
    with state_module.use_identity(TARGET), pytest.raises(asyncio.CancelledError):
        asyncio.run(wanxin._send_assist_action(target["wanxin_observation"], "strip", 110))
    original = copy.deepcopy(target["wanxin_observation"]["pending"])
    assert original["msg_id"] == (100 if recover_during_unwind else 0)
    op_id = original["resource_op_id"]
    assert accounting.current_operation(IDENTITY, op_id)["msg_id"] == original["msg_id"]
    assert (CHAT, 100) in receipts["pending_tasks"]
    received = event(".剥离咒源 @target_one", "【剥离咒源成功】\n@provider_one 替 @target_one "
                     "剥下一段阴罗残咒。幡面煞气被削去 120 点。")
    assert observe(received)
    assert not target["wanxin_observation"]["pending"]
    assert not receipts["pending_tasks"]
    assert target["wanxin_observation"]["commission"]["id"] == 0
    assert accounting.current_operation(IDENTITY, op_id)["phase"] == "complete"
    assert value()["value"] == 1880


@pytest.mark.parametrize("corruption", ["account", "op_id", "command", "module", "missing_op"])
def test_unbound_native_result_preserves_conflicting_pending_evidence(receipts, corruption):
    record = prepare(".化功为煞 10000")
    finalize(record, detached=True)
    pending = receipts["pending_tasks"][CHAT, 100]
    if corruption == "account":
        pending["account_id"] = ACCOUNT + 1
    elif corruption == "op_id":
        pending["op_id"] = "f" * 32
    elif corruption == "command":
        pending["cmd"] = ".化功为煞 20000"
    elif corruption == "module":
        pending["source_module"] = "wrong_module"
    else:
        pending.pop("op_id")
    before = copy.deepcopy(receipts)
    assert not observe(event(".化功为煞 10000", CONVERT))
    assert receipts == before


def test_failed_receipt_adoption_does_not_consume_native_pending_or_result(receipts):
    record = prepare(".化功为煞 10000")
    finalize(record, detached=True)
    assert persistence.save_state()
    before = copy.deepcopy(receipts)
    conn = persistence.get_db_conn()
    conn.execute(f"CREATE TEMP TRIGGER fail_receipt_adoption BEFORE INSERT ON identity_runtime_state "
                 f"WHEN NEW.send_as_id = {IDENTITY} BEGIN SELECT RAISE(ABORT, 'receipt failure'); END")
    try:
        assert not observe(event(".化功为煞 10000", CONVERT))
        assert receipts == before
    finally:
        conn.execute("DROP TRIGGER fail_receipt_adoption")
    assert observe(event(".化功为煞 10000", CONVERT))
    assert accounting.current_operation(IDENTITY, record["op_id"])["phase"] == "complete"
    assert not receipts["pending_tasks"]
    assert cultivation.cultivation_balance(IDENTITY)["value"] == 490000


@pytest.mark.parametrize("delay", [1, 100])
def test_old_bound_completion_does_not_try_to_bind_a_newer_unresolved_send(receipts, delay):
    record = prepare(".化功为煞 10000")
    bind(record)
    received = event(".化功为煞 10000", CONVERT, end=110.8)
    assert observe(received)
    finalize(record, at=110)
    newer = prepare(".化功为煞 10000", now=110 + delay)
    balance = cultivation.cultivation_balance(IDENTITY)
    assert yinluo.handle_yinluo_resource_reply(received, now=1000)
    assert not receipts["pending_tasks"]
    assert accounting.current_operation(IDENTITY, newer["op_id"]) == newer
    assert cultivation.cultivation_balance(IDENTITY) == balance
