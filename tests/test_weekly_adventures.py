"""Real-message strategies and bounded, restart-safe weekly ownership."""
import asyncio
import copy
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from model import activity_occupancy, app_weekly_adventures, app_xuangu, state
from model.features import weekly_adventures as weekly
from tools import safety_watchdog


@pytest.fixture
def env(monkeypatch):
    now = [1794538000.0]
    records = {}
    members = [{"identity_id": i, "username": f"member{i}", "linggen": "金"} for i in range(1, 6)]
    sends = []
    monkeypatch.setattr(weekly, "_LOCK", asyncio.Lock())
    monkeypatch.setattr(weekly.time, "time", lambda: now[0])
    enabled = {"yuejing"}

    async def send(command, **kwargs):
        assert kwargs["send_guard"]()
        sends.append((command, kwargs))
        return SimpleNamespace(id=100 + len(sends))

    ctx = SimpleNamespace(records=lambda: records, enabled=lambda kind: kind in enabled,
        available=lambda _i: True, members=lambda kind: members[:2] if kind == "yinming" else members,
        target=lambda: (-100, 42), save=lambda: None, send=send, bot_ids=lambda: [99],
        topic=lambda event: event.topic_id, event_time=lambda event: event.date.timestamp(),
        unsent=lambda *_: False, notice=AsyncMock())
    return SimpleNamespace(ctx=ctx, records=records, sends=sends, members=members, now=now, enabled=enabled)


def event(env, text, *, reply=0, sender=99, chat=-100, topic=42, at=None, mid=999):
    return SimpleNamespace(raw_text=text, reply_to_msg_id=reply, sender_id=sender, chat_id=chat,
        topic_id=topic, date=datetime.fromtimestamp(env.now[0] if at is None else at, timezone.utc), id=mid)


async def open_run(env, kind="yuejing"):
    env.enabled.clear()
    env.enabled.add(kind)
    await weekly.scheduler(env.ctx, env.now[0])
    reply = 100 + len(env.sends)
    await weekly.game_event(env.ctx, event(env, f"【{weekly.KINDS[kind]['label']}】@member1 要出发，发 {weekly.KINDS[kind]['join']} 加入。", reply=reply))
    run = env.records["runs"][kind]
    names = "、".join("@" + m["username"] for m in run["members"])
    start = {"yuejing": f"【越京夜行】夜色里，{names} 潜入越国京城。",
             "tianlan": f"【天澜草原】{names} 一行进了草原。",
             "yinming": "【阴冥之地】@member1（辨路）、@member2（感魂） 不见了踪影。"}[kind]
    await weekly.game_event(env.ctx, event(env, start))
    assert run["bound"] and run["phase"] == "running"
    return run


@pytest.mark.parametrize("sender,chat,age,confirmed", [
    (1, -10, 0, True),
    (2, -10, 0, False),
    (1, -11, 0, False),
    (1, -10, 90, False),
])
def test_log_group_confirmation_reaches_weekly_state_only_for_fresh_admin(env, monkeypatch, sender, chat, age, confirmed):
    from model import control

    monkeypatch.setattr(control, "LOG_GROUP_ID", -10)
    monkeypatch.setattr(control, "ADMIN_IDS", {1})
    monkeypatch.setattr(control, "reply_log_group_message", AsyncMock())
    monkeypatch.setattr(app_weekly_adventures, "_context", lambda: env.ctx)
    backfill, save = Mock(), Mock()
    monkeypatch.setattr(app_weekly_adventures, "_backfill_weekly_receipts", backfill)
    monkeypatch.setattr(app_weekly_adventures, "_save", save)

    async def run():
        record = await open_run(env)
        record.update(phase="held", requires_reconciliation=True)
        message = event(env, ".周常 确认结束 yuejing " + record["id"],
                        sender=sender, chat=chat, at=env.now[0] - age)
        handled = await control.handle_log_group_command(message)
        assert handled is (sender == 1 and chat == -10)
        assert (record["phase"] == "operator_confirmed") is confirmed
        assert record["requires_reconciliation"] is (not confirmed)
        assert backfill.call_count == save.call_count == int(confirmed)

    asyncio.run(run())


def test_yinming_constraint_solver_including_nonadjacent_relative_clues():
    for clue, road in [
        ("死路不在左边。感魂者：阴魂不在左边。", "左"),
        ("死路在阴魂的右手边。感魂者：阴魂不在中边。", ""),
        ("死路在阴魂的右手边。感魂者：阴魂守在正中那条路上。", "左"),
        ("死路在阴魂的左手边。感魂者：阴魂守在正中那条路上。", "右"),
        ("新线索。感魂者：阴魂不在左边。", ""),
    ]:
        assert weekly.yinming_path("辨路者：" + clue) == road
    clue = "辨路者：死路在阴魂的右手边。感魂者：阴魂不在中边。"
    assert weekly.yinming_path(clue, allow_risk=True) in {"中", "右"}
    assert weekly.yinming_path(clue, known_dead="中") == "右"
    assert weekly.yinming_path(clue, known_dead="右") == "中"


def test_real_public_panels_use_only_documented_actions():
    rows = json.loads((Path(__file__).parent / "fixtures/weekly_adventures_live_20261113.json").read_text())
    members = [{"identity_id": i, "username": f"member{i}", "linggen": "金"} for i in range(1, 6)]
    found = set()
    for row in rows:
        text = row["text"]
        kind = "tianlan" if "天澜草原·第" in text or "以鼎收兽·" in text else "yinming" if "【阴冥 第" in text or "【暴风山 第" in text else "yuejing"
        parsed = weekly.panel(kind, text, members[:2] if kind == "yinming" else members)
        assert parsed, text
        stage, seconds, actions = parsed
        assert stage > 0 and seconds in {45, 60, 90, 120}
        found.add(kind)
        # Ambiguous clues are intentionally held; no unsafe random road.
        assert actions, text
        for identity, command in actions:
            assert 1 <= identity <= 5 and command.startswith(".")
    assert found == set(weekly.KINDS)


def test_full_owned_yuejing_flow_and_quota_stop(env):
    async def run():
        record = await open_run(env)
        for number in range(1, 4):
            text = f"第 {number}/3 轮查访：.查访 地点（60 秒，去票多的一处）。"
            await weekly.game_event(env.ctx, event(env, text))
        await weekly.game_event(env.ctx, event(env,
            "【越京夜行·闯宫】青纹（土） 铁罗（水） 冰妖（木） 叶蛇（金）\n.迎战 血侍名"))
        await weekly.game_event(env.ctx, event(env,
            "第 1/5 回合 教主 15/15 真凰符 2 张\n征兆：教主周身血光凝成一层厚茧，脚下纹丝不动。\n.破幕 / .闪身 / .强攻"))
        assert env.sends[-1][0] == ".真凰符"
        count = len(env.sends)
        result = "【越京夜行·战果】教主伏法\n" + "\n".join(f"@member{i}：本周有奖次数已满" for i in range(1, 6))
        await weekly.game_event(env.ctx, event(env, result))
        assert record["phase"] == "done"
        assert all(q["used"] == 2 for q in env.records["quotas"]["yuejing"].values())
        env.now[0] += 3600
        await weekly.scheduler(env.ctx, env.now[0])
        assert len(env.sends) == count
        await weekly.game_event(env.ctx, event(env, result))
        assert len(env.records["receipt_keys"]) == 1
    asyncio.run(run())


@pytest.mark.parametrize("changes", [{"sender": 98}, {"chat": -101}, {"topic": 43}, {"at": 1794537000}])
def test_foreign_events_never_drive_actions(env, changes):
    async def run():
        await open_run(env)
        count = len(env.sends)
        await weekly.game_event(env.ctx, event(env, "第 1/3 轮查访：.查访 地点", **changes))
        assert len(env.sends) == count
    asyncio.run(run())


def test_collision_and_timeout_hold_without_reopen(env):
    async def run():
        record = await open_run(env)
        await weekly.game_event(env.ctx, event(env, "【越京夜行】@another 要夜探越国京城，加入。"))
        assert record["phase"] == "held" and record["requires_reconciliation"]
        count = len(env.sends)
        env.now[0] += 99999
        await weekly.scheduler(env.ctx, env.now[0])
        assert len(env.sends) == count
    asyncio.run(run())


def test_complete_owned_terminal_receipt_can_close_a_collision_hold(env):
    async def run():
        record = await open_run(env)
        await weekly.game_event(env.ctx, event(env, "【越京夜行】@another 要夜探越国京城，加入。"))
        assert record["phase"] == "held"
        result = "【越京夜行·战果】教主遁走，无功而返。\n" + "\n".join(f"@member{i} 修为+100 灵石+10" for i in range(1, 6))
        await weekly.game_event(env.ctx, event(env, result))
        assert record["phase"] == "done" and not record["requires_reconciliation"]
    asyncio.run(run())


def test_unknown_open_is_not_replayed(env):
    async def fail(*_a, **_k):
        return None
    env.ctx.send = fail
    async def run():
        await weekly.scheduler(env.ctx, env.now[0])
        record = env.records["runs"]["yuejing"]
        assert record["phase"] == "held"
        attempt = copy.deepcopy(record["attempts"])
        await weekly.scheduler(env.ctx, env.now[0] + 86400)
        assert record["attempts"] == attempt
    asyncio.run(run())


@pytest.mark.parametrize("kind", list(weekly.KINDS))
def test_only_timed_join_and_stage_actions_use_reactive_queue(env, kind):
    async def run():
        await open_run(env, kind)
        assert env.sends[0][1]["reactive"] is False
        joins = [kwargs for command, kwargs in env.sends if command == weekly.KINDS[kind]["join"]]
        assert joins and all(kwargs["reactive"] for kwargs in joins)
    asyncio.run(run())


def test_foreign_player_start_during_join_invalidates_queued_member(env):
    normal_send = env.ctx.send

    async def interrupted_send(command, **kwargs):
        if kwargs["send_as_id"] == 5:
            weekly.observe(env.ctx, event(env,
                "你加入了队伍（5/5）：@member1、@member2、@member3、@other、@member4\n"
                "【越京夜行】夜色里，@member1、@member2、@member3、@other、@member4 潜入越国京城。", mid=888))
            assert not kwargs["send_guard"]()
            return None
        return await normal_send(command, **kwargs)

    env.ctx.send = interrupted_send

    async def run():
        await weekly.scheduler(env.ctx, env.now[0])
        await weekly.game_event(env.ctx, event(env,
            "【越京夜行】@member1 要夜探越国京城，发 .同往 加入。", reply=101))
        record = env.records["runs"]["yuejing"]
        assert record["phase"] == "held" and not record["bound"]
        assert record["collision_message_id"] == 888 and "other" in record["collision_names"]
        assert "其他玩家" in record["last_error"]
        assert [kwargs["send_as_id"] for _, kwargs in env.sends] == [1, 2, 3, 4]

    asyncio.run(run())


def test_tianlan_owned_ack_waits_for_separate_named_recruitment(env):
    async def run():
        env.enabled.clear(); env.enabled.add("tianlan")
        await weekly.scheduler(env.ctx, env.now[0])
        await weekly.game_event(env.ctx, event(env, "你在草原边上等同行的人。", reply=101))
        record = env.records["runs"]["tianlan"]
        assert record["phase"] == "opening" and record["open_acknowledged"]
        assert len(env.sends) == 1
        await weekly.game_event(env.ctx, event(env,
            "【天澜草原】@member1 要横穿天澜草原，2 分钟内发 .同行 加入（元婴以上，不同主魂，3–5 人）。"))
        assert record["phase"] == "joining" and len(env.sends) == 5
    asyncio.run(run())


def test_yinming_real_recruitment_needs_no_literal_join_word(env):
    async def run():
        env.enabled.clear(); env.enabled.add("yinming")
        await weekly.scheduler(env.ctx, env.now[0])
        await weekly.game_event(env.ctx, event(env,
            "【阴冥之地】@member1 立在鬼雾边上，等一位同行者。2 分钟内发 .同入阴冥（元婴以上，另一主魂）。进去后每一步的线索都公开在群里。", reply=101))
        assert env.records["runs"]["yinming"]["phase"] == "joining"
        assert env.sends[-1][0] == ".同入阴冥"
    asyncio.run(run())


def test_manual_confirmation_requires_held_record_and_exact_id(env):
    async def run():
        record = await open_run(env)
        assert not (await weekly.confirm_finished(env.ctx, "yuejing", record["id"]))[0]
        await weekly.hold(env.ctx, record, "test")
        assert not (await weekly.confirm_finished(env.ctx, "yuejing", "wrong"))[0]
        attempts = copy.deepcopy(record["attempts"])
        assert (await weekly.confirm_finished(env.ctx, "yuejing", record["id"]))[0]
        assert record["attempts"] == attempts and record["phase"] == "operator_confirmed"
    asyncio.run(run())


def test_new_stage_invalidates_old_actions_before_lock_release(env):
    async def run():
        record = await open_run(env)
        record.update(stage=1, observed_stage=1)
        weekly.observe(env.ctx, event(env, "第 2/3 轮：.查访 秦府夜宴"))
        assert record["observed_stage"] == 2
        count = len(env.sends)
        assert not await weekly.send(env.ctx, record, 1, ".查访 城南道观", 1, env.now[0] + 60)
        assert len(env.sends) == count
    asyncio.run(run())


def test_owned_dead_road_receipt_allows_one_evidence_based_alternative(env):
    async def run():
        record = await open_run(env, "yinming")
        text = "【阴冥 第1/6步】雾中三条路\n辨路者：死路在阴魂的右手边。感魂者：阴魂不在中边。\n阳气 8/8"
        await weekly.game_event(env.ctx, event(env, text))
        first = env.sends[-1][0]
        road = first[-1]
        await weekly.game_event(env.ctx, event(env, f"@member1 选了{road}边——一头撞上死路，阳气 -1。"))
        await weekly.game_event(env.ctx, event(env, text.replace("8/8", "7/8")))
        assert env.sends[-1][0].startswith(".往 ") and env.sends[-1][0] != first
        count = len(env.sends)
        await weekly.game_event(env.ctx, event(env, text.replace("8/8", "7/8")))
        assert len(env.sends) == count and record["phase"] == "running"
    asyncio.run(run())


def test_yinming_ambiguous_fight_uses_documented_counter():
    members = [{"identity_id": 1}, {"identity_id": 2}]
    text = "【暴风山 第1/3招】破招口诀。辨路者看温天仁的起手，不是「沉肩」就是「扫腿」。\n阳气 {}/8"
    assert weekly.panel("yinming", text.format(8), members)[2] == [(2, ".应招 下压")]
    assert weekly.panel("yinming", text.format(6), members)[2] == [(2, ".应招 下压")]


def test_weekly_realm_selection_excludes_two_jiedan_accounts(monkeypatch):
    monkeypatch.setattr(app_weekly_adventures, "_config", lambda: {"participant_identity_ids": [1, 2, 3, 4, 5]})
    monkeypatch.setattr(app_weekly_adventures, "get_identity_ids", lambda: [1, 2, 3, 4, 5])
    monkeypatch.setattr(app_weekly_adventures, "get_send_as_profile", lambda i: {
        "username": f"member{i}", "realm": "元婴后期" if i < 4 else "结丹后期"})
    assert len(app_weekly_adventures._members("tianlan")) == 3
    assert len(app_weekly_adventures._members("yinming")) == 3
    assert len(app_weekly_adventures._members("yuejing")) == 5


def test_restart_keeps_unknown_ownership(monkeypatch):
    records = {"weekly": {"runs": {"yinming": {"phase": "running", "members": [{"identity_id": 1}]}}}}
    monkeypatch.setattr(app_weekly_adventures, "_backfill_weekly_receipts", lambda: None)
    monkeypatch.setattr(app_weekly_adventures, "get_expedition_run_state", lambda: records)
    monkeypatch.setattr(activity_occupancy, "get_expedition_run_state", lambda: records)
    app_weekly_adventures.reset_weekly_on_startup()
    assert records["weekly"]["runs"]["yinming"]["requires_reconciliation"]
    assert "周常" in activity_occupancy.get_activity_block_reason(1)


@pytest.fixture
def daily_env(monkeypatch):
    records = {}
    now = time.time()
    config = {"enabled": True, "auto_daily": True, "participant_identity_ids": [1, 2, 3, 4, 5]}
    monkeypatch.setattr(app_xuangu, "get_expedition_config", lambda: {"xuangu": config})
    monkeypatch.setattr(app_xuangu, "get_expedition_run_state", lambda: records)
    monkeypatch.setattr(app_xuangu, "_save", lambda _r: None)
    monkeypatch.setattr(app_xuangu, "_preflight_error", lambda _i: "")
    monkeypatch.setattr(app_xuangu, "_enabled", lambda _i=None: True)
    monkeypatch.setattr(app_xuangu, "get_replica_group_ids", lambda: [-200])
    monkeypatch.setattr(app_xuangu, "get_miniapp_auto_config", lambda: {"cave_public_entry_url": "fixture"})
    monkeypatch.setattr(app_xuangu, "get_send_as_profile", lambda i: {"username": f"member{i}"})
    monkeypatch.setattr(app_xuangu, "_context", lambda: object())
    from model.features import cave_treasure_runtime as cave
    inventory = AsyncMock(return_value={"ok": True})
    opening = AsyncMock(return_value=True)
    monkeypatch.setattr(cave, "run_cave_public_inventory", inventory)
    monkeypatch.setattr(app_xuangu.xuangu, "start_xuangu_roster", opening)
    bags = {"2": {"source": "cave_inventory_miniapp", "updated_at": now, "items": {"古府寻踪珠": 3}}}
    monkeypatch.setattr(app_xuangu, "get_storage_bag_records", lambda: bags)
    return SimpleNamespace(records=records, config=config, now=now, inventory=inventory, opening=opening, bags=bags)


def test_daily_uses_fresh_ticket_owner_and_persists_one_attempt(daily_env):
    e = daily_env
    async def run():
        await app_xuangu._run_daily(e.now)
        assert e.opening.await_count == 1
        assert e.opening.call_args.args[1][0] == "member2"
        assert e.opening.call_args.kwargs["auto_daily"]
        await app_xuangu._run_daily(e.now + 7200)
        assert e.opening.await_count == 1
    asyncio.run(run())


def test_xuangu_weekly_cap_counts_rewards_and_uncertain_reserved_days(daily_env):
    e = daily_env
    week = weekly.week_key(e.now)
    e.records["xuangu_daily"] = {"quota_days": {
        "2026-11-09": {"week": week, "reason": "rewarded"},
        "2026-11-10": {"week": week, "reason": "reserved"},
        "2026-11-11": {"week": week, "reason": "rewarded"},
    }}
    assert app_xuangu._weekly_used(e.records["xuangu_daily"], e.now) == 3
    asyncio.run(app_xuangu._run_daily(e.now))
    e.opening.assert_not_awaited()
    e.inventory.assert_not_awaited()
    assert app_xuangu._weekly_used(e.records["xuangu_daily"], e.now + 7 * 86400) == 0


def test_xuangu_failed_no_ticket_check_does_not_reserve_weekly_quota(daily_env):
    e = daily_env
    e.bags.clear()
    asyncio.run(app_xuangu._run_daily(e.now))
    assert app_xuangu._weekly_used(e.records["xuangu_daily"], e.now) == 0
    assert "attempt_day" not in e.records["xuangu_daily"]


@pytest.mark.parametrize("mode", ["no_ticket", "stale", "wrong_source", "failed_refresh", "already_rewarded", "prior_attempt"])
def test_daily_never_spends_ticket_without_all_guards(daily_env, mode):
    e = daily_env
    if mode == "no_ticket":
        e.bags["2"]["items"] = {}
    elif mode == "stale":
        e.bags["2"]["updated_at"] = e.now - 300
    elif mode == "wrong_source":
        e.bags["2"]["source"] = "old_text"
    elif mode == "failed_refresh":
        e.inventory.return_value = {"ok": False}
    elif mode == "already_rewarded":
        e.records["xuangu_daily"] = {"rewards": {"1": {"day": app_xuangu._day(e.now)}}}
    else:
        e.records["xuangu_daily"] = {"attempt_day": app_xuangu._day(e.now)}
    asyncio.run(app_xuangu._run_daily(e.now))
    e.opening.assert_not_awaited()


def test_daily_reward_receipt_does_not_count_other_names(daily_env):
    roster = {"my_members": [{"identity_id": 1, "username": "member1"}, {"identity_id": 2, "username": "member2"}]}
    event = SimpleNamespace(raw_text="@member1：修为+6000\n@other：修为+6000", id=99)
    app_xuangu._record_daily_outcome(roster, event, "收拢遗宝", daily_env.now)
    assert set(daily_env.records["xuangu_daily"]["rewards"]) == {"1"}


@pytest.mark.parametrize("changes", [{}, {"sender_id": 2}, {"chain_id": "weekly:yuejing:1794538000:2"},
    {"op_id": "forged"}, {"text": ".同往"}, {"priority": "chain"}, {"_epoch": 1000}])
def test_watchdog_weekly_gap_requires_exact_metadata(changes):
    previous = {"_epoch": 1000, "op_id": "previous"}
    current = {"_epoch": 1002, "sender_id": 1, "text": ".破幕", "source_module": "组队周常",
        "priority": "urgent_reactive", "op_id": "weekly:yuejing:1794538000:5:1:.破幕", "chain_id": "weekly:yuejing:1794538000:5"}
    current.update(changes)
    assert safety_watchdog.is_safe_global_gap_pair(previous, current) is (not changes)


@pytest.mark.parametrize("kind,command", [("tianlan", ".同行"), ("yuejing", ".同往"), ("yinming", ".同入阴冥")])
@pytest.mark.parametrize("mode", ["valid", "wrong_stage", "wrong_kind", "wrong_sender", "wrong_chain", "open"])
def test_watchdog_timed_join_requires_exact_recruitment_metadata(kind, command, mode):
    previous = {"_epoch": 1000, "op_id": "previous"}
    stage = 1 if mode == "wrong_stage" else 0
    op_kind = next(k for k in weekly.KINDS if k != kind) if mode == "wrong_kind" else kind
    text = weekly.KINDS[kind]["open"] if mode == "open" else command
    current = {"_epoch": 1002, "sender_id": 2 if mode == "wrong_sender" else 1,
               "text": text, "source_module": "组队周常", "priority": "urgent_reactive",
               "op_id": f"weekly:{op_kind}:123:{stage}:1:{text}",
               "chain_id": f"weekly:{op_kind}:123:{stage}"}
    if mode == "wrong_chain":
        current["chain_id"] = "weekly:yuejing:456:0"
    assert safety_watchdog.is_safe_global_gap_pair(previous, current) is (mode == "valid")


def test_watchdog_repeated_action_requires_new_stage_same_run_and_ten_seconds():
    previous = {"_epoch": 1000, "sender_id": 1, "text": ".闪避", "source_module": "组队周常", "priority": "urgent_reactive",
                "op_id": "weekly:tianlan:123:5:1:.闪避", "chain_id": "weekly:tianlan:123:5"}
    current = {**previous, "_epoch": 1015, "op_id": "weekly:tianlan:123:6:1:.闪避", "chain_id": "weekly:tianlan:123:6"}
    assert safety_watchdog.is_safe_same_command_retry(previous, current, ".闪避")
    for change in [{"_epoch": 1005}, {"chain_id": "weekly:tianlan:456:6"}, {"op_id": previous["op_id"], "chain_id": previous["chain_id"]}]:
        assert not safety_watchdog.is_safe_same_command_retry(previous, {**current, **change}, ".闪避")
