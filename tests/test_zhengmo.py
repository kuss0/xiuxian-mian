"""Daily war ownership, recommendation, quota and uncertain-send recovery."""
import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model import app_zhengmo
from model.features import zhengmo as war

STATUS = "【正魔大战·第1季】11-12–11-25　第 2/14 天\n今日军情（正道）：斥候回报，魔道今日多半【强攻】，以【固守】应之可得双倍战功（七成把握）。\n你还没有加入本季战事：你的宗门固定正道，直接 .出征 即可。"
UNAFFILIATED = "【正魔大战·第1季】11-12–11-25\n今日军情：投效之后可见。\n你还没有加入本季战事：.投效 魔道 或 .投效 正道，然后 .出征。"
RESULT = "【正魔大战·燕家堡】你率众固守，魔道这回强攻。\n战功+20，修为+8572，灵石+454。本季出征 1/7 天。"


@pytest.fixture
def env(monkeypatch):
    now = [1794538000.0]
    runs, sends = {}, []
    config = {"enabled": True, "hour": 0, "group_role": "secondary", "identities": [{"identity_id": 1, "faction": ""}]}
    monkeypatch.setattr(war.time, "time", lambda: now[0])
    monkeypatch.setattr(war, "_START_LOCK", asyncio.Lock())
    monkeypatch.setattr(war, "_LOCKS", {})
    async def send(command, **kwargs):
        assert kwargs["send_guard"]()
        sends.append((command, kwargs))
        return SimpleNamespace(id=100 + len(sends))
    ctx = SimpleNamespace(config=lambda: config, runs=lambda: runs, enabled=lambda: True,
        members=lambda ids: ([{"identity_id": i, "root_id": i, "username": f"member{i}"} for i in ids], ""),
        member_enabled=lambda i: True, root=lambda i: i, target=lambda role: (-100, 42),
        save=lambda: None, send=send, bot_ids=lambda: [99], notice=AsyncMock())
    return SimpleNamespace(now=now, runs=runs, sends=sends, config=config, ctx=ctx)


def event(env, text, *, reply=101, sender=99, chat=-100, topic=42, mid=999):
    return SimpleNamespace(raw_text=text, reply_to_msg_id=reply, sender_id=sender, chat_id=chat,
        id=mid, date=datetime.fromtimestamp(env.now[0], timezone.utc),
        reply_to=SimpleNamespace(reply_to_top_id=topic))


def test_fixed_faction_follows_own_counter_once_and_stops(env):
    async def run():
        await war.scheduler(env.ctx, env.now[0])
        await war.game_event(env.ctx, event(env, STATUS))
        assert [c for c, _ in env.sends] == [".正魔大战", ".出征 固守"]
        await war.game_event(env.ctx, event(env, RESULT, reply=102))
        assert env.runs["1"]["phase"] == "done"
        assert env.runs["1"]["personal_points"] == 20
        await war.game_event(env.ctx, event(env, RESULT, reply=102))
        env.now[0] += 3600
        await war.scheduler(env.ctx, env.now[0])
        assert len(env.sends) == 2
        env.now[0] += 86400
        await war.scheduler(env.ctx, env.now[0])
        assert env.sends[-1][0] == ".正魔大战" and len(env.sends) == 3
    asyncio.run(run())


@pytest.mark.parametrize("status,phase", [(UNAFFILIATED, "skipped"),
    (STATUS.replace("你的宗门固定正道，直接 .出征 即可。", "你：正道 · 战功 20 · 出征 1/7 天 · 今日已出征"), "done"),
    ("正魔大战尚未开战", "unavailable"),
    (STATUS.replace("今日军情（正道）", "今日军情（魔道）"), "blocked")])
def test_no_faction_done_and_unconfirmed_intelligence_do_not_march(env, status, phase):
    async def run():
        await war.start(env.ctx, 1)
        await war.game_event(env.ctx, event(env, status))
        assert env.runs["1"]["phase"] == phase
        await war.scheduler(env.ctx, env.now[0] + 600)
        assert [c for c, _ in env.sends] == [".正魔大战"]
    asyncio.run(run())


@pytest.mark.parametrize("changes", [{"sender": 98}, {"chat": -101}, {"topic": 43}, {"reply": 555}, {"reply": 0}, {"mid": 100}])
def test_foreign_or_unanchored_status_never_drives_march(env, changes):
    async def run():
        await war.start(env.ctx, 1)
        await war.game_event(env.ctx, event(env, STATUS, **changes))
        assert len(env.sends) == 1 and env.runs["1"]["phase"] == "querying"
    asyncio.run(run())


def test_forwarded_or_config_changed_reply_is_ignored(env):
    async def run():
        await war.start(env.ctx, 1)
        sample = event(env, STATUS)
        sample.forwarded = True
        await war.game_event(env.ctx, sample)
        assert len(env.sends) == 1
        env.config["enabled"] = False
        await war.game_event(env.ctx, event(env, STATUS))
        assert len(env.sends) == 1
    asyncio.run(run())


def test_uncertain_march_only_requeries_then_accepts_already_done(env):
    async def run():
        await war.start(env.ctx, 1)
        normal = env.ctx.send
        async def send(command, **kwargs):
            if command.startswith(".出征"):
                env.sends.append((command, kwargs))
                return None
            return await normal(command, **kwargs)
        env.ctx.send = send
        await war.game_event(env.ctx, event(env, STATUS))
        assert env.runs["1"]["phase"] == "retry"
        env.now[0] += 301
        await war.scheduler(env.ctx, env.now[0])
        assert [c for c, _ in env.sends] == [".正魔大战", ".出征 固守", ".正魔大战"]
        status = STATUS.replace("你的宗门固定正道，直接 .出征 即可。", "你：正道 · 战功 20 · 出征 1/7 天 · 今日已出征")
        await war.game_event(env.ctx, event(env, status, reply=103))
        assert env.runs["1"]["phase"] == "done" and len(env.sends) == 3
    asyncio.run(run())


def test_query_retries_are_bounded_across_restart(env, monkeypatch):
    async def run():
        await war.start(env.ctx, 1)
        monkeypatch.setattr(app_zhengmo, "_context", lambda: env.ctx)
        monkeypatch.setattr(app_zhengmo, "mark_dirty", lambda: None)
        for _ in range(2):
            app_zhengmo.reset_zhengmo_runtime_on_startup()
            env.now[0] += 1
            await war.scheduler(env.ctx, env.now[0])
        assert env.runs["1"]["query_attempts"] == 3
        app_zhengmo.reset_zhengmo_runtime_on_startup()
        await war.scheduler(env.ctx, env.now[0])
        assert env.runs["1"]["phase"] == "blocked" and len(env.sends) == 3
    asyncio.run(run())


def test_delayed_result_and_old_day_cannot_trigger_new_march(env):
    async def run():
        await war.start(env.ctx, 1)
        await war.game_event(env.ctx, event(env, STATUS))
        env.now[0] += 91
        await war.game_event(env.ctx, event(env, RESULT, reply=102))
        assert env.runs["1"]["phase"] == "marching"
        await war.scheduler(env.ctx, env.now[0])
        assert env.runs["1"]["phase"] == "retry"
        env.now[0] += 86400
        await war.game_event(env.ctx, event(env, STATUS, reply=101))
        assert len(env.sends) == 2
    asyncio.run(run())


@pytest.mark.parametrize("entry", [{"identity_id": True}, {"identity_id": 9}, {"identity_id": 1, "faction": "random"}])
def test_invalid_configuration_fails_closed(entry):
    with pytest.raises(ValueError):
        war.normalize_config({"identities": [entry]}, {1, 2}, lambda i: i)


def test_same_soul_cannot_be_configured_twice():
    with pytest.raises(ValueError):
        war.normalize_config({"identities": [{"identity_id": 1}, {"identity_id": 2}]}, {1, 2}, lambda i: 1)


def test_send_guard_rechecks_after_queue_wait(env):
    async def run():
        await war.start(env.ctx, 1)
        captured = env.sends[0][1]["send_guard"]
        assert captured()
        before = copy.deepcopy(env.config)
        env.config["identities"] = []
        assert not captured()
        env.config.update(before)
        env.now[0] += 61
        assert not captured()
    asyncio.run(run())
