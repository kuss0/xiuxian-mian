"""Real-message replay and boundary failures for newly ported expeditions."""
import asyncio
import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import app_xuangu, state
from model import app_replica, runtime
from model.features import xuangu
from model.features.expedition_store import FubenFlowStore


SAMPLES = json.loads((Path(__file__).parent / "fixtures/expedition_live_20261112.json").read_text())
ROUNDS = [sample for sample in SAMPLES if "·第" in sample["text"]]
NAMES = ["BitDrifter", "CqdOps", "LazyDaemo", "SilLoop", "Davecy"]


@pytest.fixture(autouse=True)
def isolated_state():
    previous = copy.deepcopy(state._meta_state)
    state.set_expedition_run_state({})
    state.set_hunt_run_state({})
    yield
    state._meta_state.clear()
    state._meta_state.update(previous)


def make_event(sample, *, now=None, reply=None):
    return SimpleNamespace(
        raw_text=sample["text"], id=sample["message_id"], chat_id=sample["chat_id"],
        date=datetime.fromtimestamp(time.time() if now is None else now, timezone.utc),
        reply_to_msg_id=sample["reply_to_msg_id"] if reply is None else reply,
        reply_to=SimpleNamespace(reply_to_top_id=sample["topic_id"],
                                 reply_to_msg_id=sample["reply_to_msg_id"] if reply is None else reply),
    )


def make_context(now=None):
    now = time.time() if now is None else now
    roster = {
        "flow_id": "fixture", "roster_id": "fixture", "room_id": "abcd", "phase": "running",
        "leader_username": NAMES[0], "leader_identity_id": 1, "is_captain": True,
        "members": NAMES[:], "my_members": [{"username": name, "identity_id": i + 1} for i, name in enumerate(NAMES)],
        "game_chat_id": -1002083016447, "game_topic_id": 0, "seen_at": now - 5,
        "expires_at": now + 900,
    }
    records = {"xuangu_auto": {"rosters": {"fixture": roster}}}
    store = FubenFlowStore(lambda: records, lambda value: None)
    ctx = SimpleNamespace(
        flows=store.collection("xuangu_auto", "rosters"),
        messages=SimpleNamespace(game_command=AsyncMock(return_value=SimpleNamespace(id=700)),
                                 audit=AsyncMock(), notice_by_chat=AsyncMock()),
        identities=SimpleNamespace(participation_enabled=lambda *_: True),
        commands=SimpleNamespace(act=".玄骨行动", finish=".玄骨收尾", disband=".解散玄骨"),
        finish_choice="收拢遗宝", sleep=AsyncMock(),
    )
    return ctx, roster


def test_expired_never_entered_assembly_cancels_once_and_requires_own_receipt():
    ctx, roster = make_context()
    roster.update(phase="held", auto_daily=True, requires_reconciliation=True, expires_at=time.time() - 10)
    async def run():
        await xuangu.run_xuangu_auto_scheduler(ctx, time.time())
        ctx.messages.game_command.assert_awaited_once()
        assert ctx.messages.game_command.call_args.args[0] == ".解散玄骨"
        assert roster["requires_reconciliation"]
        await xuangu.run_xuangu_auto_scheduler(ctx, time.time() + 600)
        ctx.messages.game_command.assert_awaited_once()
        # Real 11-13 no-team wording: foreign or anonymous replies cannot release.
        event = SimpleNamespace(raw_text="你当前没有玄骨队伍。", id=701, chat_id=roster["game_chat_id"],
                                reply_to_msg_id=699, reply_to=None)
        assert not await xuangu.handle_xuangu_game_event(ctx, event)
        assert roster["requires_reconciliation"]
        event.reply_to_msg_id = 700
        assert await xuangu.handle_xuangu_game_event(ctx, event)
        assert roster["phase"] == "settled" and not roster["requires_reconciliation"]
    asyncio.run(run())


@pytest.mark.parametrize("changes", [{"entered": True}, {"enter_requested_at": 1}, {"current_round": 1},
    {"command_attempts": {"1:.进入玄骨": {"status": "unknown"}}}, {"auto_daily": False},
    {"is_captain": False}, {"expires_at": time.time() + 9999}])
def test_uncertain_or_entered_room_is_not_automatically_cancelled(changes):
    ctx, roster = make_context()
    roster.update(phase="held", auto_daily=True, requires_reconciliation=True, expires_at=time.time() - 10)
    roster.update(changes)
    asyncio.run(xuangu.run_xuangu_auto_scheduler(ctx, time.time()))
    ctx.messages.game_command.assert_not_called()
    assert roster["requires_reconciliation"]


def test_expired_assembly_does_not_cancel_in_changed_group():
    ctx, roster = make_context()
    ctx.target = lambda: (-999, 0)
    roster.update(phase="held", auto_daily=True, requires_reconciliation=True, expires_at=time.time() - 10)
    asyncio.run(xuangu.run_xuangu_auto_scheduler(ctx, time.time()))
    ctx.messages.game_command.assert_not_called()
    assert roster["requires_reconciliation"]


@pytest.mark.parametrize("sample", ROUNDS, ids=lambda sample: sample["source"])
def test_real_xuangu_panels_provide_canonical_actions(sample):
    assert all(sample[key] for key in ("source", "module", "family", "event_type", "text"))
    panel = xuangu.parse_xuangu_round(sample["text"])
    assert panel["actions"]
    assert all(".玄骨行动" not in action and "\n" not in action for action in panel["actions"])
    for role in panel["roles"]:
        action, _ = xuangu.decide_xuangu_action(panel["round"], role, panel)
        assert action in panel["actions"]


def test_low_soul_keeps_round_two_required_actions():
    panel = {"round": 2, "soul": 59, "actions": ["引蛇", "破敌", "护队", "镇魂"]}
    actions = {xuangu.decide_xuangu_action(2, role, panel)[0] for role in ("探禁", "攻伐", "护队", "镇魂", "御傀")}
    assert {"引蛇", "破敌", "护队", "镇魂"} <= actions


def test_low_soul_preserves_final_soul_recovery():
    panel = {"round": 5, "soul": 45, "actions": ["收神", "护队", "镇魂", "破敌"]}
    assert xuangu.decide_xuangu_action(5, "御傀", panel)[0] == "收神"
    assert xuangu.decide_xuangu_action(5, "探禁", panel)[0] == "镇魂"


def test_round_deadline_invalidates_queued_command(monkeypatch):
    now = [1000.0]
    ctx, roster = make_context(now[0])
    roster.update(current_round=1, round_deadline=1005)
    monkeypatch.setattr(xuangu.time, "time", lambda: now[0])

    async def delayed(command, **kwargs):
        assert kwargs["send_guard"]()
        now[0] = 1006
        assert not kwargs["send_guard"]()

    ctx.messages.game_command = delayed
    asyncio.run(xuangu._send_xuangu_command(ctx, ".玄骨行动 1 验骨", send_as_id=1))


def test_an_unowned_finish_broadcast_cannot_finish_the_only_local_room():
    ctx, roster = make_context()
    sample = next(sample for sample in SAMPLES if sample["message_id"] == 1560000)
    assert xuangu._match_roster_for_outcome(ctx, "残图异动", time.time(), event=make_event(sample)) is None
    assert roster["phase"] == "running"


def test_xuangu_action_adapter_uses_bounded_reactive_queue(monkeypatch):
    monkeypatch.setattr(app_xuangu, "hunt_target", lambda _: (-100, 0))
    monkeypatch.setattr(app_xuangu, "_enabled", lambda *_: True)
    monkeypatch.setattr(app_xuangu, "get_activity_block_reason", lambda *a, **k: "")
    send = AsyncMock(return_value=SimpleNamespace(id=700))
    monkeypatch.setattr(app_xuangu, "send_game_command", send)
    asyncio.run(app_xuangu._send(".玄骨行动 1 验骨", send_as_id=1, send_guard=lambda: True,
                               deadline=time.time() + 20))
    assert send.call_args.kwargs["priority"] == "urgent_reactive"
    assert 0 < send.call_args.kwargs["queue_timeout"] <= 20
    assert "skip_throttle" not in send.call_args.kwargs


def test_five_member_real_rounds_fit_existing_pacing_and_confirm_receipts(monkeypatch):
    now = [1000.0]
    ctx, roster = make_context(now[0])
    last_global = [now[0]]
    last_identity = {1: now[0]}  # Captain has just sent 进入玄骨.
    sent = []
    monkeypatch.setattr(xuangu.time, "time", lambda: now[0])
    monkeypatch.setattr(app_xuangu, "hunt_target", lambda _: (-1002083016447, 0))
    monkeypatch.setattr(app_xuangu, "_enabled", lambda *_: True)
    monkeypatch.setattr(app_xuangu, "get_activity_block_reason", lambda *a, **k: "")

    async def paced(command, **kwargs):
        assert kwargs["priority"] == "urgent_reactive"
        identity = kwargs["send_as_id"]
        now[0] = max(now[0], last_global[0] + runtime.URGENT_REACTIVE_SEND_GAP_MAX_SEC,
                     last_identity.get(identity, 0) + runtime.IDENTITY_SEND_GAP_MIN_SEC)
        assert kwargs["operation_check"](), (command, now[0], roster.get("round_deadline"))
        last_global[0] = last_identity[identity] = now[0]
        now[0] += 0.4
        sent.append((command, identity, now[0]))
        return SimpleNamespace(id=700 + len(sent))

    monkeypatch.setattr(app_xuangu, "send_game_command", paced)
    ctx.messages.game_command = app_xuangu._send

    async def run():
        for sample in ROUNDS:
            start = now[0]
            panel = xuangu.parse_xuangu_round(sample["text"])
            message = make_event(sample, now=start)
            before = len(sent)
            await xuangu._on_xuangu_round(ctx, message, panel, start)
            actions = roster["round_actions"][str(panel["round"])]
            assert len(sent) - before == len(actions) == 5
            assert all(row[2] < start + 30 for row in sent[before:])
            for name, action in actions.items():
                ack = "已提交第%d轮【%s%s】，截止前可修改。" % (
                    panel["round"], "亮招·" if action["special"] else "", action["action"])
                message.reply_to_msg_id = action["msg_id"]
                await xuangu._on_xuangu_action_reply(ctx, message, ack, now[0])
                assert actions[name]["status"] == "confirmed"
            now[0] = start + 31
        assert roster["round_done"] == [1, 2, 3, 4, 5]
        sample = next(sample for sample in SAMPLES if sample["message_id"] == 1560000)
        await xuangu._on_xuangu_outcome(ctx, make_event(sample, now=now[0]), "残图异动", now[0])
        assert len(sent) == 25
        message = make_event(sample, now=now[0], reply=700 + len(sent))
        await xuangu._on_xuangu_outcome(ctx, message, "残图异动", now[0])
        assert sent[-1][0] == ".玄骨收尾 收拢遗宝"
        terminal = next(sample for sample in SAMPLES if sample["message_id"] == 1560002)
        await xuangu._on_xuangu_outcome(ctx, make_event(terminal, now=now[0], reply=700 + len(sent)), "争取全收", now[0])
        assert roster["phase"] == "settled"

    asyncio.run(run())


def test_missing_pool_never_dispatches_a_guessed_defence():
    ctx, roster = make_context()
    panel = {"round": 1, "roles": {"探禁": NAMES[0]}, "actions": [], "remaining": 30}
    asyncio.run(xuangu._on_xuangu_round(ctx, make_event(ROUNDS[0]), panel, time.time()))
    ctx.messages.game_command.assert_not_called()
    assert "动作池无法识别" in roster["last_error"]


def test_unknown_room_record_survives_expiry_and_keeps_activity_ownership():
    from model.activity_occupancy import get_activity_block_reason
    ctx, roster = make_context()
    roster.update(phase="held", requires_reconciliation=True, expires_at=time.time() - 3600)
    state.set_expedition_run_state({"xuangu_auto": {"rosters": {"fixture": roster}}})
    ctx.flows.cleanup(time.time())
    assert ctx.flows.active(time.time()) == [roster]
    assert get_activity_block_reason(1, include_replica=False) == "玄骨遗府进行中"


@pytest.mark.parametrize("realm", ["筑基后期", "", "未知境界"])
def test_ineligible_xuangu_profile_stops_before_opening(monkeypatch, realm):
    monkeypatch.setattr(app_xuangu, "get_send_as_profile", lambda _: {"realm": realm})
    assert "结丹初期" in app_xuangu._preflight_error(1)


def test_roster_needs_five_distinct_members():
    assert xuangu.parse_xuangu_roster_command(".开启玄骨 @aaa @bbb @ccc @ddd") is None
    assert xuangu.parse_xuangu_roster_command(".开启玄骨 @aaa @bbb @ccc @ddd @aaa") is None


def test_cangkun_missing_status_does_not_recommend_extra_greed():
    advice = app_replica._format_cangkun_decision_advice({"title": "第三幕·玉矶阁取宝"}, "请队长使用 .苍坤抉择 1/2/3")
    assert "选1 先取卷轴" in advice
    assert "状态数据不完整" in advice


def test_next_round_cancels_queued_actions_before_reducer_lock(monkeypatch):
    ctx, roster = make_context()
    roster.update(current_round=1, round_deadline=time.time() + 25)
    monkeypatch.setattr(app_xuangu, "_enabled", lambda *_: True)
    monkeypatch.setattr(app_xuangu, "get_game_bot_ids", lambda: [99])
    monkeypatch.setattr(app_xuangu, "hunt_target", lambda _: (-1002083016447, 0))
    monkeypatch.setattr(app_xuangu, "_context", lambda: ctx)
    message = make_event(ROUNDS[1])
    message.sender_id = 99

    async def run():
        lock = asyncio.Lock()
        monkeypatch.setattr(app_xuangu, "_LOCK", lock)
        async with lock:
            task = asyncio.create_task(app_xuangu.handle_xuangu_game_event(message))
            await asyncio.sleep(0)
            assert roster["observed_round"] == 2
            assert not task.done()
            await xuangu._send_xuangu_command(ctx, ".玄骨行动 1 验骨", send_as_id=1)
            ctx.messages.game_command.assert_not_called()
        await task
    asyncio.run(run())


@pytest.mark.parametrize("phase", ["held", "interrupted"])
def test_uncertain_roster_never_resumes_from_finish_prompt(phase):
    ctx, roster = make_context()
    roster.update(phase=phase, requires_reconciliation=True)
    sample = next(s for s in SAMPLES if s["message_id"] == 1560000)
    message = make_event(sample)
    message.room_id = "abcd"
    asyncio.run(xuangu._on_xuangu_outcome(ctx, message, "残图异动", time.time()))
    ctx.messages.game_command.assert_not_called()
    assert roster["requires_reconciliation"]


@pytest.mark.parametrize("choices,expected", [
    ("争取全收（不可选）\n收拢遗宝（可选）", "收拢遗宝"),
    ("争取全收（不可选）\n收拢遗宝（不可选）\n立即撤离（可选）", "立即撤离"),
    ("争取全收（不可选）", None),
])
def test_finish_uses_only_server_available_choices(choices, expected):
    ctx, roster = make_context()
    ctx.finish_choice = "争取全收"
    message = make_event(ROUNDS[-1])
    message.raw_text, message.room_id = choices, "abcd"
    asyncio.run(xuangu._on_xuangu_outcome(ctx, message, "残图异动", time.time()))
    if expected:
        assert ctx.messages.game_command.call_args.args[0] == ".玄骨收尾 " + expected
    else:
        ctx.messages.game_command.assert_not_called()
        assert roster["requires_reconciliation"]


def test_late_finish_prompt_does_not_dispatch():
    ctx, roster = make_context()
    message = make_event(ROUNDS[-1], now=time.time() - 40)
    message.raw_text, message.room_id = "收拢遗宝（可选）", "abcd"
    roster["seen_at"] = time.time() - 50
    asyncio.run(xuangu._on_xuangu_outcome(ctx, message, "残图异动", time.time()))
    ctx.messages.game_command.assert_not_called()


def test_disband_holds_ownership_until_owned_game_cancellation():
    from model.activity_occupancy import get_activity_block_reason
    ctx, roster = make_context()
    state.set_expedition_run_state({"xuangu_auto": {"rosters": {"fixture": roster}}})
    async def run():
        await xuangu._xuangu_disband(ctx, roster, time.time(), "operator")
        assert roster["requires_reconciliation"]
        assert get_activity_block_reason(1, include_replica=False)
        message = make_event(ROUNDS[0])
        message.room_id = "abcd"
        await xuangu._on_xuangu_outcome(ctx, message, "集结取消", time.time())
        assert not roster["requires_reconciliation"]
        assert not get_activity_block_reason(1, include_replica=False)
    asyncio.run(run())


@pytest.mark.parametrize("own,fresh,key,chat,expected", [
    (True, True, "fixture", -100, True), (False, True, "fixture", -100, False),
    (True, False, "fixture", -100, False), (True, True, "wrong", -100, False),
    (True, True, "fixture", -200, False),
])
def test_xuangu_operator_confirmation_requires_exact_fresh_owned_record(own, fresh, key, chat, expected):
    ctx, roster = make_context()
    roster.update(phase="held", requires_reconciliation=True, assemble_chat_id=-100)
    ctx.messages.group_ids = lambda: [-100, -200]
    ctx.messages.listener_account_id = lambda _: 1
    ctx.messages.is_listener_self = lambda *_: own
    ctx.messages.group_message = AsyncMock()
    message = SimpleNamespace(raw_text=".确认玄骨结束 " + key, chat_id=chat, client=None,
                              date=datetime.fromtimestamp(time.time() - (0 if fresh else 90), timezone.utc))
    asyncio.run(xuangu.handle_xuangu_confirmation(ctx, message))
    assert (not roster["requires_reconciliation"]) is expected


@pytest.mark.parametrize("realm,player,current,expected", [
    ("结丹后期", 1, True, True), ("结丹后期", 2, True, False),
    ("结丹后期", 1, False, False), ("", 1, True, False),
])
def test_read_only_dwelling_profile_never_applies_wrong_or_changed_identity(monkeypatch, realm, player, current, expected):
    from model.features import cave_treasure_runtime as cave
    from model import profile_observation
    monkeypatch.setattr(cave.MiniAppIdentityOwner, "capture", lambda _: SimpleNamespace(is_current=lambda: current))
    monkeypatch.setattr(cave, "is_cave_public_identity_available", lambda _: True)
    monkeypatch.setattr(cave, "_public_entry_allowed", lambda: True)
    monkeypatch.setattr(cave, "_public_entry_lock", lambda _: asyncio.Lock())
    monkeypatch.setattr(cave, "save_state", lambda: True)
    monkeypatch.setattr(cave, "_load_cave_public_identity_session", AsyncMock(return_value={
        "ok": True, "result": {"data": {"overview": {"player_id": player, "cultivation_level": realm}}}}))
    apply = Mock(return_value={"realm": realm})
    monkeypatch.setattr(profile_observation, "apply_profile_observation", apply)
    result = asyncio.run(cave.run_cave_public_profile(1, "https://t.me/fanrenxiuxian_bot?startapp=df_TEST"))
    assert result["ok"] is expected
    assert apply.called is expected


def test_shared_log_and_assembly_group_reaches_xuangu_only_for_admin(monkeypatch):
    from model import control
    monkeypatch.setattr(control, "LOG_GROUP_ID", -100)
    monkeypatch.setattr(control, "ADMIN_IDS", {1})
    handler = AsyncMock(return_value=True)
    monkeypatch.setattr(app_xuangu, "handle_xuangu_group_command", handler)
    message = SimpleNamespace(chat_id=-100, sender_id=1, raw_text=".开启玄骨 @aaa @bbb @ccc @ddd @eee")
    assert asyncio.run(control.handle_log_group_command(message))
    handler.assert_awaited_once_with(message)
    message.sender_id = 2
    assert not asyncio.run(control.handle_log_group_command(message))
    assert handler.await_count == 1


def test_shared_group_listener_requires_configured_client(monkeypatch):
    from model import app_message_log
    owner, stranger = object(), object()
    monkeypatch.setattr(app_xuangu, "_get_replica_event_listener_account_id", lambda _: 0)
    monkeypatch.setattr(app_xuangu, "get_replica_group_ids", lambda: [-100])
    monkeypatch.setattr(app_xuangu, "get_replica_listener_account_map", lambda: {"-100": 1})
    monkeypatch.setattr(app_message_log, "get_all_clients", lambda: {1: owner})
    assert app_xuangu._listener_account_id(SimpleNamespace(chat_id=-100, client=owner)) == 1
    assert app_xuangu._listener_account_id(SimpleNamespace(chat_id=-100, client=stranger)) == 0


def test_actual_five_person_run_confirms_last_actions_from_owned_phase_replies():
    live = json.loads((Path(__file__).parent / "fixtures/xuangu_live_20261113.json").read_text())
    now = time.time()
    ctx, roster = make_context(now)
    roster.clear()
    roster.update(copy.deepcopy(live["roster"]))
    roster.update(flow_id="fixture", roster_id="fixture", phase="running", current_round=5,
                  round_done=[], expires_at=now + 900)
    for actions in roster["round_actions"].values():
        for action in actions.values():
            action["status"] = "pending"
    transitions = [sample for sample in live["events"] if "【玄骨遗府·" in sample["text"]]
    acks = [sample for sample in live["events"] if "已提交第" in sample["text"]]
    assert len(transitions) == 5 and len(acks) == 20

    async def run():
        first = make_event(transitions[0], now=transitions[0]["server_event_at"], reply=7310786)
        await xuangu.handle_xuangu_game_event(ctx, first)
        assert all(action["status"] == "pending" for action in roster["round_actions"]["1"].values())
        # Direct replies may arrive after a shared panel already advanced us.
        for sample in transitions:
            await xuangu.handle_xuangu_game_event(ctx, make_event(sample, now=sample["server_event_at"]))
        assert sum(action.get("confirmation_kind") == "phase_transition"
                   for actions in roster["round_actions"].values() for action in actions.values()) == 5
        roster["phase"] = "settled"
        # Queued acks remain evidence after finishing, without resuming sends.
        for sample in acks:
            assert await xuangu._on_xuangu_action_reply(
                ctx, make_event(sample, now=sample["server_event_at"]), sample["text"], now)
    asyncio.run(run())
    assert roster["round_done"] == [1, 2, 3, 4, 5]
    assert all(action["status"] == "confirmed"
               for actions in roster["round_actions"].values() for action in actions.values())
    ctx.messages.game_command.assert_not_called()
