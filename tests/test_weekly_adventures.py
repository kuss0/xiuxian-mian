"""Real-message strategies and bounded, restart-safe weekly ownership."""
import asyncio
import copy
import itertools
import json
import threading
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
    if kind == "qipai":
        await weekly.game_event(env.ctx, event(env, f"@member5 入议，现有 5 位长老：{names}", reply=105))
        await weekly.game_event(env.ctx, event(env, "【七派密议·开议】", reply=106))
        assert run["bound"] and run["phase"] == "running"
        return run
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


@pytest.mark.parametrize("kind,limit,size", [(kind, 3 if kind == "qipai" else 2, size)
                                          for kind in ("qipai", "yuejing", "tianlan") for size in (5, 6, 9)])
def test_quota_selection_never_opens_with_only_exhausted_members(env, kind, limit, size):
    env.enabled.clear()
    env.enabled.add(kind)
    env.members[:] = [{"identity_id": i, "username": f"member{i}", "linggen": "金"} for i in range(1, size + 1)]
    field = "day" if kind == "qipai" else "week"
    current = weekly.period(kind, env.now[0])
    env.records["quotas"] = {kind: {str(i): {field: current, "used": limit if i <= 5 else 0}
                                  for i in range(1, size + 1)}}
    asyncio.run(weekly.scheduler(env.ctx, env.now[0]))
    if size == 5:
        assert not env.sends
    else:
        chosen = env.records["runs"][kind]["members"]
        assert any(member["identity_id"] > 5 for member in chosen)
        assert len(chosen) <= (8 if kind == "qipai" else 5)
        assert chosen[0]["identity_id"] == 6


@pytest.mark.parametrize("count", [1, 2, 3])
def test_yuejing_mixed_party_all_local_members_fight(env, count):
    text = "【越京夜行·闯宫】青纹（土） 铁罗（水） 冰妖（木） 叶蛇（金）\n.迎战 血侍名"
    _stage, _seconds, actions = weekly.panel("yuejing", text, env.members[:count])
    assert {identity for identity, _command in actions} == set(range(1, count + 1))
    assert len({command for _identity, command in actions}) == count


def test_named_teams_share_quota_and_do_not_open_parallel_games(env):
    async def run():
        first = SimpleNamespace(**vars(env.ctx), team_name="一队", run_key=lambda kind: "一队:" + kind,
                                accept_run=lambda run: run.get("team") == "一队", route=["秦府夜宴"] * 3, use_talisman=False)
        second = SimpleNamespace(**vars(env.ctx), team_name="二队", run_key=lambda kind: "二队:" + kind,
                                 accept_run=lambda run: run.get("team") == "二队")
        await weekly.scheduler(first, env.now[0])
        await weekly.scheduler(second, env.now[0])
        assert len(env.sends) == 1
        record = env.records["runs"]["一队:yuejing"]
        assert record["route"] == ["秦府夜宴"] * 3 and not record["use_talisman"]
        record.update(phase="done")
        env.records["quotas"] = {"yuejing": {str(m["identity_id"]): {"week": weekly.period("yuejing", env.now[0]), "used": 2} for m in env.members}}
        await weekly.scheduler(second, env.now[0])
        assert len(env.sends) == 1
    asyncio.run(run())


def test_yuejing_selected_route_is_used(env):
    stage = weekly.panel("yuejing", "第 2/3 轮查访：.查访 地点", env.members, route=["秦府夜宴"] * 3)
    assert stage and all(command == ".查访 秦府夜宴" for _identity, command in stage[2])


def test_three_member_yuejing_can_start_and_unknown_tianlan_station_uses_sneak(env):
    env.members[:] = env.members[:3]
    asyncio.run(weekly.scheduler(env.ctx, env.now[0]))
    assert env.sends[0][0] == ".夜探越京"
    prompt = "【天澜草原·第1站】前方巡哨动静不明。\n90 秒内投票：.潜行 或 .斩哨（踪迹 0）"
    assert weekly.panel("tianlan", prompt, env.members)[2] == [(i, ".潜行") for i in (1, 2, 3)]


def test_game_daohao_is_recognized_in_departure_and_reward(env):
    env.members[0]["username"] = "玄平翁"
    run = {"kind": "tianlan", "members": env.members}
    text = "【天澜草原】@玄平翁、@member2、@member3 一行进了草原。"
    assert weekly.departure(run, text) == ["玄平翁", "member2", "member3"]
    assert weekly.reward_line("玄平翁", "@玄平翁 修为+100 灵石+10")


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
    qipai = json.loads((Path(__file__).parent / "fixtures/qipai_live_20261113.json").read_text())
    council_members = [{"identity_id": 1, "username": "CqdOps"}, {"identity_id": 2, "username": "LazyDaemo"}]
    parsed = [weekly.panel("qipai", row["text"], council_members) for row in qipai]
    assert any(p and p[2] and p[2][0][1].startswith(".点将 ") for p in parsed)
    assert any(p and p[2] and p[2][0][1] == ".准" for p in parsed)
    found.add("qipai")
    assert found == set(weekly.KINDS)


def test_foreign_replies_and_forwarded_panels_do_not_advance(env):
    async def run():
        record = await open_run(env)
        sent = len(env.sends)
        text = "第 1/3 轮查访：.查访 地点"
        await weekly.game_event(env.ctx, event(env, text, reply=123456))
        forwarded = event(env, text)
        forwarded.forwarded = True
        await weekly.game_event(env.ctx, forwarded)
        assert len(env.sends) == sent and record["stage"] == 0
    asyncio.run(run())


def test_proven_unsent_action_retries_only_within_original_window(env):
    async def run():
        record = await open_run(env)
        normal = env.ctx.send
        attempts = []
        async def send(command, **kwargs):
            attempts.append(command)
            if len(attempts) == 1:
                return None
            return await normal(command, **kwargs)
        env.ctx.send, env.ctx.unsent = send, lambda *_: True
        await weekly.game_event(env.ctx, event(env, "第 1/3 轮查访：.查访 地点"))
        assert record["phase"] == "running" and len(attempts) == 1
        await weekly.scheduler(env.ctx, env.now[0])
        assert len(attempts) == 1
        env.now[0] += 16
        await weekly.scheduler(env.ctx, env.now[0])
        assert len(attempts) == 6 and "pending_actions" not in record
        assert next(a for a in record["attempts"].values() if a["stage"] == 1)["tries"] == 2
    asyncio.run(run())


def test_proven_unsent_open_exhaustion_releases_without_consuming_quota(env):
    async def run():
        env.ctx.send = AsyncMock(return_value=None)
        env.ctx.unsent = lambda *_: True
        await weekly.scheduler(env.ctx, env.now[0])
        record = env.records["runs"]["yuejing"]
        for _ in range(2):
            env.now[0] += 16
            await weekly.scheduler(env.ctx, env.now[0])
        assert record["phase"] == "failed" and not record["requires_reconciliation"]
        assert env.ctx.send.await_count == 3
        assert not env.records.get("quotas", {}).get("yuejing")
    asyncio.run(run())


def test_fresh_higher_panel_resumes_interrupted_run_without_replaying_old_stage(env):
    async def run():
        record = await open_run(env)
        await weekly.game_event(env.ctx, event(env, "第 1/3 轮查访：.查访 地点"))
        record.update(phase="interrupted", requires_reconciliation=True)
        sent = len(env.sends)
        await weekly.game_event(env.ctx, event(env, "第 2/3 轮查访：.查访 地点"))
        assert record["phase"] == "running" and record["stage"] == 2
        assert all(c == ".查访 散修集市" for c, _ in env.sends[sent:])
    asyncio.run(run())


def test_already_backfilled_terminal_closes_run_without_increment(env):
    async def run():
        record = await open_run(env)
        env.records["receipt_keys"] = ["-100:999"]
        env.records["quotas"] = {"yuejing": {str(i): {"week": weekly.week_key(env.now[0]), "used": 1} for i in range(1, 6)}}
        result = "【越京夜行·战果】\n" + "\n".join(f"@member{i} 修为+10" for i in range(1, 6))
        await weekly.game_event(env.ctx, event(env, result))
        assert record["phase"] == "done"
        assert all(q["used"] == 1 for q in env.records["quotas"]["yuejing"].values())
    asyncio.run(run())


@pytest.mark.parametrize("confirmation", ["你引开了两位仙师。", "你正被两位仙师追着跑，顾不上这边。", "【诱敌】@member5 飞身掠出，两位大仙师果然追了上去"])
def test_tianlan_decoy_requires_real_confirmation(env, confirmation):
    async def run():
        record = await open_run(env, "tianlan")
        await weekly.game_event(env.ctx, event(env, "【天澜草原·圣女追杀】"))
        attempt = next(a for a in record["attempts"].values() if a["command"] == ".诱敌")
        await weekly.game_event(env.ctx, event(env, confirmation, reply=attempt["msg_id"]))
        assert record["decoy_ids"] == [5]
        sent = len(env.sends)
        await weekly.game_event(env.ctx, event(env, "【以鼎收兽·第1/3回合】灵兽仰起头长啸"))
        assert {k["send_as_id"] for _, k in env.sends[sent:]} == {1, 2, 3, 4}
    asyncio.run(run())


def test_qipai_daily_three_then_next_day_and_anonymous_terminal(env):
    async def run():
        record = await open_run(env, "qipai")
        env.records["quotas"] = {"qipai": {str(i): {"day": weekly.period("qipai", env.now[0]), "used": 2} for i in range(1, 6)}}
        await weekly.game_event(env.ctx, event(env, "【密议·第 1 关】主帅 @member1 点将：黄枫谷、掩月宗。诸位长老 .准 或 .驳"))
        await weekly.game_event(env.ctx, event(env, "【七派密议·散议】七派守住三关，越国无恙！\n5 位长老各得修为、灵石（按境界，获胜翻倍）。", reply=100 + len(env.sends)))
        assert record["phase"] == "done"
        assert all(q["used"] == 3 for q in env.records["quotas"]["qipai"].values())
        sent = len(env.sends)
        env.now[0] += 600
        await weekly.scheduler(env.ctx, env.now[0])
        assert len(env.sends) == sent
        env.now[0] += 86400
        await weekly.scheduler(env.ctx, env.now[0])
        assert env.sends[-1][0] == ".开密议" and len(env.sends) == sent + 1
    asyncio.run(run())


def test_qipai_roster_intruder_dismisses_and_waits_for_own_receipt(env):
    async def run():
        env.enabled.clear()
        env.enabled.add("qipai")
        await weekly.scheduler(env.ctx, env.now[0])
        await weekly.game_event(env.ctx, event(env, "【七派密议】@member1 召集密议，发 .入议", reply=101))
        record = env.records["runs"]["qipai"]
        await weekly.game_event(env.ctx, event(env, "@intruder 入议，现有 3 位长老：@member1、@member2、@intruder", reply=102))
        assert env.sends[-1][0] == ".散议" and record["phase"] == "dissolving"
        mid = 100 + len(env.sends)
        await weekly.game_event(env.ctx, event(env, "这一场密议散了。", reply=456))
        assert record["phase"] == "dissolving"
        await weekly.game_event(env.ctx, event(env, "这一场密议散了。", reply=mid))
        assert record["phase"] == "cancelled" and not record["requires_reconciliation"]
        assert not env.records.get("quotas", {}).get("qipai")
    asyncio.run(run())


def test_failed_unstarted_council_cancels_once_and_does_not_reopen_same_day(env):
    async def run():
        env.enabled.clear()
        env.enabled.add("qipai")
        await weekly.scheduler(env.ctx, env.now[0])
        await weekly.game_event(env.ctx, event(env, "【七派密议】@member1 召集密议，发 .入议", reply=101))
        record = env.records["runs"]["qipai"]
        await weekly.hold(env.ctx, record, "成员发送受限")
        env.now[0] = record["deadline"] + 46
        await weekly.scheduler(env.ctx, env.now[0])
        assert env.sends[-1][0] == ".散议" and record["requires_reconciliation"]
        mid = 100 + len(env.sends)
        await weekly.game_event(env.ctx, event(env, "这一场密议散了。", reply=mid))
        assert record["phase"] == "cancelled" and not record["requires_reconciliation"]
        count = len(env.sends)
        env.now[0] += 3600
        await weekly.scheduler(env.ctx, env.now[0])
        assert len(env.sends) == count
        assert not env.records.get("quotas", {}).get("qipai")
    asyncio.run(run())


def test_unconfirmed_opening_council_is_not_cancelled(env):
    async def run():
        record = await open_run(env, "qipai")
        await weekly.hold(env.ctx, record, "开议结果待核对")
        record["bound"] = False
        env.now[0] = record["deadline"] + 46
        count = len(env.sends)
        await weekly.scheduler(env.ctx, env.now[0])
        assert len(env.sends) == count and record["requires_reconciliation"]
    asyncio.run(run())


def test_real_foreign_council_full_replay_never_votes_or_claims_rewards(env):
    rows = json.loads((Path(__file__).parent / "fixtures/qipai_live_20261113.json").read_text())
    async def run():
        env.enabled.clear()
        env.enabled.add("qipai")
        env.now[0] = min(r["server_event_at"] for r in rows) - 1
        await weekly.scheduler(env.ctx, env.now[0])
        for row in rows:
            env.now[0] = row["server_event_at"]
            await weekly.game_event(env.ctx, event(env, row["text"], sender=row["sender_id"], reply=row["reply_to_msg_id"], mid=row["message_id"]))
        assert [c for c, _ in env.sends] == [".开密议"]
        assert not env.records.get("quotas", {}).get("qipai")
    # Real bot IDs must be trusted so this tests ownership, not sender rejection.
    bot_ids = {r["sender_id"] for r in rows if r["text"].startswith(("【", "@"))}
    env.ctx.bot_ids = lambda: bot_ids
    asyncio.run(run())


def test_qipai_unlinked_anonymous_terminal_cannot_close_our_council(env):
    async def run():
        record = await open_run(env, "qipai")
        await weekly.game_event(env.ctx, event(env, "【七派密议·散议】\n5 位长老各得修为、灵石"))
        assert record["phase"] == "running" and not record.get("terminal_pending")
        assert not env.records.get("quotas", {}).get("qipai")
    asyncio.run(run())


def test_yinming_external_spirit_actor_is_waited_for_not_impersonated(env):
    async def run():
        env.enabled.clear()
        env.enabled.add("yinming")
        await weekly.scheduler(env.ctx, env.now[0])
        await weekly.game_event(env.ctx, event(env, "【阴冥之地】@member1 立在鬼雾，发 .同入阴冥", reply=101))
        await weekly.game_event(env.ctx, event(env, "【阴冥之地】@member1（辨路）、@outsider（感魂） 不见了踪影。", reply=12345))
        record = env.records["runs"]["yinming"]
        assert record["yinming_roles"] == {"辨路": 1, "感魂": None}
        sent = len(env.sends)
        await weekly.game_event(env.ctx, event(env, "【暴风山 第1/3招】辨路者看温天仁的起手「沉肩」\n沉肩→侧闪 扫腿→下压 虚晃→不动"))
        assert record["phase"] == "running" and record["stage"] == 7 and len(env.sends) == sent
        assert record["missing_members"] == ["member2"]
    asyncio.run(run())


def test_startup_replays_saved_terminal_locally_once(env, monkeypatch, tmp_path):
    async def run():
        record = await open_run(env, "qipai")
        record.update(phase="interrupted", requires_reconciliation=True)
        text = "【七派密议·散议】\n5 位长老各得修为、灵石"
        row = {"text": text, "chat_id": -100, "topic_id": 42, "sender_id": 99, "message_id": 1001,
               "reply_to_msg_id": 106, "server_event_at": env.now[0]}
        day = datetime.fromtimestamp(env.now[0], app_weekly_adventures.TZ_LOCAL).strftime("%Y-%m-%d")
        (tmp_path / (day + ".log")).write_text(json.dumps(row, ensure_ascii=False) + "\n")
        monkeypatch.setattr(app_weekly_adventures, "MESSAGES_DIR", tmp_path)
        monkeypatch.setattr(app_weekly_adventures, "_context", lambda: env.ctx)
        await app_weekly_adventures._recover_terminal_receipts(env.now[0])
        assert record["phase"] == "done" and not record["requires_reconciliation"]
        await app_weekly_adventures._recover_terminal_receipts(env.now[0])
        assert all(q["used"] == 1 for q in env.records["quotas"]["qipai"].values())
    asyncio.run(run())


def test_startup_log_scan_does_not_block_owner_event_loop(env, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    owner_thread = threading.get_ident()
    def read(*args, **kwargs):
        assert threading.get_ident() != owner_thread
        entered.set()
        assert release.wait(2)
        return iter(())
    async def run():
        await open_run(env, "qipai")
        monkeypatch.setattr(app_weekly_adventures, "_context", lambda: env.ctx)
        monkeypatch.setattr(app_weekly_adventures, "iter_message_log_rows", read)
        task = asyncio.create_task(app_weekly_adventures._recover_terminal_receipts(env.now[0]))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), 2)
            assert not task.done()  # Owner loop is available while the file worker waits.
        finally:
            release.set()
        await task
    asyncio.run(run())


@pytest.mark.parametrize("command,stage,valid", [(".入议", 0, True), (".开议", 0, True), (".准", 2, True),
    (".点将 黄枫谷 掩月宗", 1, True), (".点将 黄枫谷", 1, False), (".投效 魔道", 1, False), (".散议", 0, False)])
def test_qipai_watchdog_exact_action_metadata(command, stage, valid):
    previous = {"_epoch": 1000, "op_id": "unrelated"}
    current = {"_epoch": 1002, "sender_id": 1, "text": command, "source_module": "组队周常", "priority": "urgent_reactive",
               "op_id": f"weekly:qipai:123:{stage}:1:{command}", "chain_id": f"weekly:qipai:123:{stage}"}
    assert safety_watchdog.is_safe_weekly_reactive_gap_pair(previous, current) is valid


def test_qipai_real_panel_sequence_updates_posterior_and_finishes_owned_run(env):
    rows = json.loads((Path(__file__).parent / "fixtures/qipai_live_20261113.json").read_text())
    usernames = ["CqdOps", "LazyDaemo", "SilLoop", "BitDrifter", "Davecy"]
    for m, username in zip(env.members, usernames):
        m["username"] = username
    async def run():
        env.enabled.clear()
        env.enabled.add("qipai")
        env.now[0] = min(r["server_event_at"] for r in rows) - 1
        await weekly.scheduler(env.ctx, env.now[0])
        record = env.records["runs"]["qipai"]
        recruit = next(r for r in rows if r["message_id"] == 1577852)
        await weekly.game_event(env.ctx, event(env, recruit["text"], reply=101))
        roster = next(r for r in rows if r["message_id"] == 1577861)
        await weekly.game_event(env.ctx, event(env, roster["text"], reply=105))
        for row in rows:
            if row["message_id"] < 1577870 or not ("【七派密议" in row["text"] or "【密议·" in row["text"]):
                continue
            env.now[0] = row["server_event_at"]
            body = row["text"].replace("8 位长老各得", "5 位长老各得")
            await weekly.game_event(env.ctx, event(env, body, mid=row["message_id"], reply=100 + len(env.sends)))
        assert record["phase"] == "done" and len(record["observations"]) == 3
        assert any(c.startswith(".点将") for c, _ in env.sends)
        assert all(q["used"] == 1 for q in env.records["quotas"]["qipai"].values())
    asyncio.run(run())


def test_qipai_inconsistent_public_evidence_never_resets_posterior():
    from model.features import council
    assert council.choice(2, [{"sects": ["黄枫谷", "掩月宗"], "sabotages": 3}]) == ()
    observed = [{"sects": ["黄枫谷", "掩月宗"], "sabotages": 2}]
    assert not set(council.choice(4, observed)) & {"黄枫谷", "掩月宗"}


def test_qipai_fast_rounds_respect_same_voter_send_gap(env):
    async def run():
        record = await open_run(env, "qipai")
        waits = []
        async def sleep(seconds):
            waits.append(seconds)
            env.now[0] += seconds
        env.ctx.sleep = sleep
        for n in (1, 2):
            await weekly.game_event(env.ctx, event(env, f"【密议·第 {n} 关】主帅 @member1 点将：黄枫谷、掩月宗。诸位长老 .准 或 .驳"))
        assert waits == [11]
        first, second = [record["attempts"][f"{stage}:2:.准"] for stage in (2, 4)]
        assert second["confirmed_at"] - first["confirmed_at"] >= 11
    asyncio.run(run())


def test_qipai_busy_rejection_releases_and_waits_for_foreign_end_without_claiming_it(env):
    async def run():
        env.enabled.clear()
        env.enabled.add("qipai")
        await weekly.scheduler(env.ctx, env.now[0])
        record = env.records["runs"]["qipai"]
        # Reproduce the observed production hold, then replay its real rejection.
        record.update(phase="held", collision=True, requires_reconciliation=True)
        rejection = "这里已经有一场密议了。.密议 看局面，.入议 加入。"
        await weekly.game_event(env.ctx, event(env, rejection, reply=987))
        assert record["phase"] == "held"
        await weekly.game_event(env.ctx, event(env, rejection, reply=101))
        assert record["phase"] == "waiting_room" and not record["requires_reconciliation"]
        assert record["foreign_busy"]
        env.now[0] += 120
        await weekly.scheduler(env.ctx, env.now[0])
        assert len(env.sends) == 1
        await weekly.game_event(env.ctx, event(env, "【七派密议·散议】\n3 位长老各得修为、灵石", reply=54321))
        assert not record["foreign_busy"] and not env.records.get("quotas", {}).get("qipai")
        assert len(env.sends) == 1
        env.now[0] += 61
        await weekly.scheduler(env.ctx, env.now[0])
        assert len(env.sends) == 1
        env.now[0] += 120
        await weekly.scheduler(env.ctx, env.now[0])
        assert [c for c, _ in env.sends] == [".开密议", ".开密议"]
        assert env.records["runs"]["qipai"] is not record
    asyncio.run(run())


def test_qipai_foreign_recruitment_during_cooldown_defers_next_open(env):
    async def run():
        record = await open_run(env, "qipai")
        record.update(phase="done", next_at=env.now[0] + 300)
        sent = len(env.sends)
        await weekly.game_event(env.ctx, event(env, "【七派密议】@outsider 召集密议，.入议 加入"))
        env.now[0] += 301
        await weekly.scheduler(env.ctx, env.now[0])
        assert len(env.sends) == sent and record["foreign_busy"]
        assert not env.records.get("quotas", {}).get("qipai")
    asyncio.run(run())


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


def test_real_talisman_terminal_skips_actor_line_and_finishes_all_quotas(env):
    row = json.loads((Path(__file__).parent / "fixtures/yuejing_talisman_terminal_20261113.json").read_text())
    usernames = ["jfdffdddd", "WalterWA2000", "Lpprceqei", "wisemole", "xianxia9527"]
    for member, username in zip(env.members, usernames):
        member["username"] = username
    env.now[0] = row["server_event_at"] - 60
    env.records["quotas"] = {"yuejing": {str(i): {"week": weekly.week_key(env.now[0]), "used": 1} for i in range(1, 6)}}

    async def run():
        await weekly.scheduler(env.ctx, env.now[0])
        record = env.records["runs"]["yuejing"]
        record.update(phase="held", bound=True, requires_reconciliation=True, last_error="结算缺少成员奖励明细")
        env.now[0] = row["server_event_at"]
        receipt = event(env, row["text"], mid=row["message_id"])
        await weekly.game_event(env.ctx, receipt)
        assert record["phase"] == "done" and not record["requires_reconciliation"]
        assert record["last_error"] is None
        assert all(q["used"] == 2 and q["source_msg_id"] == row["message_id"]
                   for q in env.records["quotas"]["yuejing"].values())
        sent = len(env.sends)
        await weekly.game_event(env.ctx, receipt)
        env.now[0] += 3600
        await weekly.scheduler(env.ctx, env.now[0])
        assert len(env.sends) == sent and len(env.records["receipt_keys"]) == 1

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


def test_actual_party_start_during_join_invalidates_missing_queued_member(env):
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
        assert record["phase"] == "joining" and not record["bound"]
        assert record["departure_pending"] and not record.get("collision")
        assert [kwargs["send_as_id"] for _, kwargs in env.sends] == [1, 2, 3, 4]
        await weekly.game_event(env.ctx, event(env,
            "【越京夜行】夜色里，@member1、@member2、@member3、@other、@member4 潜入越国京城。", mid=888, reply=12345))
        assert record["phase"] == "running" and record["bound"]
        assert [m["identity_id"] for m in record["members"]] == [1, 2, 3, 4]
        assert record["missing_members"] == ["member5"]
        await weekly.game_event(env.ctx, event(env, "第 1/3 轮查访：.查访 地点", mid=889))
        assert {k["send_as_id"] for c, k in env.sends if c.startswith(".查访")} == {1, 2, 3, 4}

    asyncio.run(run())


@pytest.mark.parametrize("prior_collision", [False, True])
def test_complete_local_tianlan_roster_with_extra_ally_keeps_ownership(env, prior_collision):
    env.members[:] = env.members[:3]

    async def run():
        env.enabled.clear(); env.enabled.add("tianlan")
        await weekly.scheduler(env.ctx, env.now[0])
        await weekly.game_event(env.ctx, event(env,
            "【天澜草原】@member1 要横穿天澜草原，2 分钟内发 .同行 加入。", reply=101))
        record = env.records["runs"]["tianlan"]
        if prior_collision:
            await weekly.game_event(env.ctx, event(env,
                "【天澜草原】@another 要横穿天澜草原，2 分钟内发 .同行 加入。"))
        await weekly.game_event(env.ctx, event(env,
            "【天澜草原】@member1、@member2、@member3、@other 一行进了草原。"))
        before = len(env.sends)
        await weekly.game_event(env.ctx, event(env,
            "【天澜草原·第1站】前方是白狼圣殿。殿前只拴着两匹马，炊烟也淡。\n90 秒内投票：.潜行 或 .斩哨（踪迹 0）"))
        if prior_collision:
            assert record["phase"] == "held" and len(env.sends) == before
        else:
            assert record["phase"] == "running" and record["bound"]
            assert [(cmd, kw["send_as_id"]) for cmd, kw in env.sends[before:]] == [(".斩哨", i) for i in [1, 2, 3]]
            assert not record.get("collision")

    asyncio.run(run())


@pytest.mark.parametrize("local_count", [3, 5])
def test_real_successful_tianlan_chain_matches_observed_player_actions(env, local_count):
    fixture = json.loads((Path(__file__).parent / "fixtures/tianlan_success_20261113.json").read_text())
    # Keep the actual successful lure in the controlled roster in both cases.
    selected = fixture["members"] if local_count == 5 else [*fixture["members"][:2], fixture["members"][-1]]
    env.members[:] = selected
    controlled = {member["identity_id"] for member in selected}
    rows = fixture["events"]
    env.ctx.target = lambda: (rows[0]["chat_id"], rows[0]["topic_id"])
    env.ctx.bot_ids = lambda: list({row["sender_id"] for row in rows if row["sender_id"] not in {m["identity_id"] for m in fixture["members"]}})

    async def run():
        env.enabled.clear(); env.enabled.add("tianlan")
        env.now[0] = rows[0]["server_event_at"] - 2
        await weekly.scheduler(env.ctx, env.now[0])
        observed = []
        for row in rows:
            env.now[0] = row["server_event_at"]
            if row["sender_id"] in controlled and row["text"].startswith("."):
                observed.append((row["sender_id"], row["text"]))
            elif row["sender_id"] in env.ctx.bot_ids():
                await weekly.game_event(env.ctx, event(env, row["text"], sender=row["sender_id"],
                    chat=row["chat_id"], topic=row["topic_id"], reply=row["reply_to_msg_id"] or 0, mid=row["message_id"]))
        emitted = [(kwargs["send_as_id"], command) for command, kwargs in env.sends
                   if command not in {".天澜草原", ".同行"}]
        # Player identities can be emitted in a different order within a vote.
        assert sorted(emitted) == sorted(observed)
        record = env.records["runs"]["tianlan"]
        assert record["phase"] == "done" and record["bound"] and not record.get("collision")
        assert all(q["used"] == 1 for q in env.records["quotas"]["tianlan"].values())
        assert len(env.records["quotas"]["tianlan"]) == local_count

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


def test_duplicate_dead_road_receipt_after_alternative_does_not_hold_or_resend(env):
    async def run():
        record = await open_run(env, "yinming")
        text = "【阴冥 第4/6步】雾中三条路\n辨路者：死路不在中边。感魂者：阴魂守在正中那条路上。\n阳气 8/8"
        await weekly.game_event(env.ctx, event(env, text, mid=13177017))
        assert env.sends[-1][0] == ".往 左"
        dead = event(env, "@member1 选了左边——一头撞上死路，折返时又耗了些力气（阳气 -1，剩 7）。", mid=13177020)
        repeat = event(env, text.replace("8/8", "7/8"), mid=13177021)
        await weekly.game_event(env.ctx, dead)
        await weekly.game_event(env.ctx, repeat)
        assert env.sends[-1][0] == ".往 右"
        count = len(env.sends)
        # Another listener can deliver the same death/panel after the first
        # listener has already sent the confirmed alternative.
        await weekly.game_event(env.ctx, dead)
        await weekly.game_event(env.ctx, repeat)
        assert record["phase"] == "running"
        assert len(env.sends) == count
        await weekly.game_event(env.ctx, event(env,
            "【阴冥 第5/6步】雾中三条路\n辨路者：死路不在右边。感魂者：阴魂守在正中那条路上。\n阳气 7/8", mid=13177025))
        assert record["stage"] == 5 and env.sends[-1][0] == ".往 右"
        assert len(env.sends) == count + 1

    asyncio.run(run())


def test_delayed_dead_road_receipt_cannot_authorize_retry_of_a_later_stage(env):
    async def run():
        record = await open_run(env, "yinming")
        clue = "辨路者：死路不在中边。感魂者：阴魂守在正中那条路上。\n阳气 8/8"
        await weekly.game_event(env.ctx, event(env, "【阴冥 第4/6步】\n" + clue))
        env.now[0] += 5
        dead = event(env, "@member1 选了左边——一头撞上死路，阳气 -1。")
        await weekly.game_event(env.ctx, dead)
        await weekly.game_event(env.ctx, event(env, "【阴冥 第4/6步】\n" + clue.replace("8/8", "7/8")))
        env.now[0] += 10
        next_panel = event(env, "【阴冥 第5/6步】\n" + clue.replace("8/8", "7/8"))
        await weekly.game_event(env.ctx, next_panel)
        assert env.sends[-1][0] == ".往 左"
        count = len(env.sends)
        await weekly.game_event(env.ctx, dead)
        await weekly.game_event(env.ctx, next_panel)
        assert record["phase"] == "running" and record["stage"] == 5
        assert record.get("retry_step") is None and record["path_dead"] == {"4": "左"}
        assert len(env.sends) == count

    asyncio.run(run())


def test_yinming_ambiguous_fight_uses_documented_counter():
    members = [{"identity_id": 1}, {"identity_id": 2}]
    text = "【暴风山 第1/3招】破招口诀：沉肩→侧闪；扫腿→不动；虚晃→下压。辨路者看温天仁的起手，不是「沉肩」就是「扫腿」。\n阳气 {}/8"
    assert weekly.panel("yinming", text.format(8), members)[2] == [(2, ".应招 侧闪")]
    assert weekly.panel("yinming", text.format(6), members)[2] == [(2, ".应招 侧闪")]


@pytest.mark.parametrize("answers", list(itertools.permutations(["侧闪", "下压", "不动"])))
@pytest.mark.parametrize("move", ["沉肩", "扫腿", "虚晃"])
def test_yinming_reads_current_round_counter_mapping(answers, move):
    counters = dict(zip(["沉肩", "扫腿", "虚晃"], answers))
    rules = "；".join(f"{clue}→{answer}" for clue, answer in counters.items())
    text = f"【暴风山 第1/3招】破招口诀：{rules}。辨路者看温天仁的起手，是「{move}」。\n阳气 8/8"
    assert weekly.panel("yinming", text, [{"identity_id": 1}, {"identity_id": 2}])[2] == [(2, ".应招 " + counters[move])]


@pytest.mark.parametrize("rules", ["", "沉肩→侧闪；扫腿→不动", "沉肩→侧闪；扫腿→侧闪；虚晃→下压"])
def test_yinming_missing_or_invalid_counter_rules_never_guess(rules):
    text = f"【暴风山 第1/3招】破招口诀：{rules}。辨路者看温天仁的起手，是「扫腿」。\n阳气 8/8"
    assert weekly.panel("yinming", text, [{"identity_id": 1}, {"identity_id": 2}])[2] == []


@pytest.mark.parametrize("terminal_id", [1563442, 1568925])
def test_real_other_players_full_yinming_success_matches_all_choices(env, terminal_id):
    fixture = json.loads((Path(__file__).parent / f"fixtures/yinming_success_{terminal_id}.json").read_text())
    env.members[:] = fixture["members"]
    rows = fixture["events"]
    ids = {m["identity_id"] for m in env.members}
    env.ctx.target = lambda: (rows[0]["chat_id"], rows[0]["topic_id"])
    env.ctx.bot_ids = lambda: list({r["sender_id"] for r in rows if r["sender_id"] not in ids})

    async def run():
        env.enabled.clear(); env.enabled.add("yinming")
        env.now[0] = rows[0]["server_event_at"] - 2
        await weekly.scheduler(env.ctx, env.now[0])
        choices = []
        for row in rows:
            env.now[0] = row["server_event_at"]
            if row["sender_id"] in ids and row["text"].startswith((".往 ", ".应招 ")):
                choices.append(row["text"])
            elif row["sender_id"] in env.ctx.bot_ids():
                await weekly.game_event(env.ctx, event(env, row["text"], sender=row["sender_id"],
                    chat=row["chat_id"], topic=row["topic_id"], reply=row["reply_to_msg_id"] or 0, mid=row["message_id"]))
        emitted = [cmd for cmd, _kwargs in env.sends if cmd.startswith((".往 ", ".应招 "))]
        assert emitted == choices and len(choices) == 9
        record = env.records["runs"]["yinming"]
        assert record["phase"] == "done" and record["last_error"] is None
        assert all(q["used"] == 1 for q in env.records["quotas"]["yinming"].values())

    asyncio.run(run())


@pytest.mark.parametrize("clue,answer", [("厚茧", "破幕"), ("影子忽然拉长", "闪身")])
def test_yuejing_without_talismans_uses_observed_base_move(clue, answer):
    text = f"第 3/5 回合　教主 5/15　血祭 40%　真凰符 0 张\n征兆：{clue}。\n.破幕 / .闪身 / .强攻"
    assert weekly.panel("yuejing", text, [{"identity_id": i} for i in range(1, 6)])[2] == [(i, "." + answer) for i in range(1, 6)]


@pytest.mark.parametrize("health,talismans", [(5, 1), (10, 2)])
def test_yuejing_guaranteed_talisman_finish_never_asks_five_manual_moves(health, talismans):
    text = f"第 3/5 回合　教主 {health}/15　血祭 80%　真凰符 {talismans} 张\n征兆：教主十指暴长。\n.破幕 / .闪身 / .强攻"
    assert weekly.panel("yuejing", text, [{"identity_id": i} for i in range(1, 6)])[2] == [(1, ".真凰符")]


@pytest.mark.parametrize("path_actor,soul_actor", [(1, 2), (2, 1)])
@pytest.mark.parametrize("moves,counter", [("沉肩」就是「扫腿", "侧闪"), ("扫腿」就是「虚晃", "不动"), ("虚晃」就是「沉肩", "下压")])
def test_real_yinming_roles_and_public_counter_instructions(env, path_actor, soul_actor, moves, counter):
    async def run():
        env.enabled.clear(); env.enabled.add("yinming")
        await weekly.scheduler(env.ctx, env.now[0])
        await weekly.game_event(env.ctx, event(env,
            "【阴冥之地】@member1 立在鬼雾边上，发 .同入阴冥。", reply=101))
        await weekly.game_event(env.ctx, event(env,
            f"【阴冥之地】一阵鬼雾卷过，@member{path_actor}（辨路）、@member{soul_actor}（感魂） 不见了踪影。"))
        record = env.records["runs"]["yinming"]
        assert record["yinming_roles"] == {"辨路": path_actor, "感魂": soul_actor}
        await weekly.game_event(env.ctx, event(env,
            "【阴冥 第1/6步】雾中三条路：左、中、右。\n辨路者：死路不在右边。感魂者：阴魂不在右边。\n阳气 8/8"))
        assert (env.sends[-1][1]["send_as_id"], env.sends[-1][0]) == (path_actor, ".往 右")
        await weekly.game_event(env.ctx, event(env,
            "【暴风山 第1/3招】温天仁出手了！破招口诀：沉肩→侧闪；扫腿→不动；虚晃→下压。"
            f"辨路者看温天仁的起手，不是「{moves}」。\n阳气 7/8　感魂者 120 秒内 .应招 侧闪/下压/不动"))
        assert (env.sends[-1][1]["send_as_id"], env.sends[-1][0]) == (soul_actor, ".应招 " + counter)

    asyncio.run(run())


def test_real_ambiguous_yuejing_seal_clue_uses_available_talisman():
    # The same clue led to blood curtain at 13176357 and blood drain at
    # 13176381. A single fixed counter cannot answer both real outcomes.
    text = ("第 2/5 回合　教主 10/15　血祭 40%　真凰符 2 张\n"
            "征兆：教主双手结印，被擒的散修身上飘起缕缕血丝。\n.破幕 / .闪身 / .强攻")
    assert weekly.panel("yuejing", text, [{"identity_id": 1}])[2] == [(1, ".真凰符")]


def test_real_five_roots_reinforce_guard_without_element_counter():
    members = [{"identity_id": i, "linggen": root} for i, root in
               [(1, "火"), (2, "雷"), (3, "木火水土金"), (4, "金木"), (5, "木水")]]
    text = "【越京夜行·闯宫】惊动25。青纹（木）　铁罗（火）　冰妖（水）　叶蛇（土）\n.迎战 血侍名"
    actions = weekly.panel("yuejing", text, members)[2]
    # At 13176330 the solo fire-root member lost 青纹; 雷 was redundantly
    # sent to help an already countered 土 guard. Reinforce that weak lane.
    assert dict(actions)[1] == dict(actions)[2] == ".迎战 青纹"
    assert {command for _, command in actions} == {".迎战 青纹", ".迎战 铁罗", ".迎战 冰妖", ".迎战 叶蛇"}


def test_yinming_balances_remaining_rewards_before_helper_runs(env):
    env.members[:] = env.members[:3]
    env.ctx.members = lambda _kind: env.members
    env.enabled.clear(); env.enabled.add("yinming")
    env.records["quotas"] = {"yinming": {str(i): {"week": weekly.week_key(env.now[0]), "used": 1} for i in [1, 2]}}

    async def run():
        await weekly.scheduler(env.ctx, env.now[0])
        record = env.records["runs"]["yinming"]
        assert [m["identity_id"] for m in record["members"]] == [3, 1]
        await weekly.game_event(env.ctx, event(env, "【走出阴冥】\n@member3 修为+100 灵石+10\n@member1 修为+100 灵石+10"))
        env.now[0] += 3600
        await weekly.scheduler(env.ctx, env.now[0])
        assert [m["identity_id"] for m in env.records["runs"]["yinming"]["members"]] == [2, 3]
        await weekly.game_event(env.ctx, event(env, "【走出阴冥】\n@member2 修为+100 灵石+10\n@member3 修为+100 灵石+10", mid=1000))
        assert all(q["used"] == 2 for q in env.records["quotas"]["yinming"].values())

    asyncio.run(run())


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


def test_new_participant_backfills_existing_receipt_without_recounting_old_members(env, monkeypatch, tmp_path):
    record = {"receipt_keys": ["-100:999"], "initialized_participants": [1], "quotas": {
        "yuejing": {"1": {"week": weekly.week_key(env.now[0]), "used": 1}}}}
    config = {"participant_identity_ids": [1, 2], "group_role": "primary"}
    local_day = datetime.fromtimestamp(env.now[0], app_weekly_adventures.TZ_LOCAL).strftime("%Y-%m-%d")
    row = {"chat_id": -100, "topic_id": 42, "message_id": 999, "sender_id": 99, "server_event_at": env.now[0],
           "text": "【越京夜行·战果】\n@member1 修为+10\n@member2 修为+10"}
    (tmp_path / (local_day + ".log")).write_text(json.dumps(row, ensure_ascii=False) + "\n")
    monkeypatch.setattr(app_weekly_adventures, "MESSAGES_DIR", tmp_path)
    monkeypatch.setattr(app_weekly_adventures, "_config", lambda: config)
    monkeypatch.setattr(app_weekly_adventures, "_context", lambda: SimpleNamespace(records=lambda: record))
    monkeypatch.setattr(app_weekly_adventures, "get_send_as_profile", lambda i: {"username": f"member{i}"})
    monkeypatch.setattr(app_weekly_adventures, "get_game_bot_ids", lambda: [99])
    monkeypatch.setattr(app_weekly_adventures, "hunt_target", lambda _: (-100, 42))
    app_weekly_adventures._backfill_weekly_receipts()
    app_weekly_adventures._backfill_weekly_receipts()
    assert record["quotas"]["yuejing"]["1"]["used"] == 1
    assert record["quotas"]["yuejing"]["2"]["used"] == 1


@pytest.fixture
def daily_env(monkeypatch):
    records = {}
    now = datetime(2026, 11, 13, 4, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(app_xuangu.time, "time", lambda: now)
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
