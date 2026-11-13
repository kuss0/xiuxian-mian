"""Server-confirmed weekly quotas and conservative voyage ownership."""
import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from model.features import hunt, hunt_weekly


@pytest.fixture
def weekly_env(monkeypatch):
    now = [1794531000.0]
    teams = [{"name": "周常队", "group_role": "secondary", "seas": ["近海"] * 3,
              "member_identity_ids": [1, 2], "auto_weekly": True}]
    members = [{"identity_id": 1, "root_id": 1, "username": "captain"},
               {"identity_id": 2, "root_id": 2, "username": "mate"}]
    weekly, runs, sends = {}, {}, []
    monkeypatch.setattr(hunt.time, "time", lambda: now[0])
    monkeypatch.setattr(hunt_weekly, "_LOCK", asyncio.Lock())
    hunt._LOCKS.clear()

    async def send(command, **kwargs):
        assert kwargs["send_guard"]()
        sends.append((command, kwargs))
        return SimpleNamespace(id=len(sends) + 200)

    ctx = SimpleNamespace(teams=lambda: copy.deepcopy(teams), members=lambda ids: (copy.deepcopy(members), ""),
                          weekly=lambda: weekly, runs=lambda: runs, save=lambda: None,
                          enabled=lambda: True, target=lambda _: (-100, 300), bot_ids=lambda: [99],
                          send=send, notice=AsyncMock())
    return SimpleNamespace(ctx=ctx, now=now, teams=teams, weekly=weekly, runs=runs, sends=sends)


def receipt(env, used, *, reply=None, sender=99, chat=-100, topic=300, text=None):
    query = env.weekly["周常队"]["query"]
    return SimpleNamespace(
        id=1000 + query["msg_id"], sender_id=sender, chat_id=chat,
        raw_text=text or f"你眼下不在任何猎妖船上。用 .出海猎妖 开船，或在别人开船时 .登船。\n本周有奖出海：{used}/5　累计出海 9 次",
        date=datetime.fromtimestamp(env.now[0], timezone.utc),
        reply_to_msg_id=query["msg_id"] if reply is None else reply,
        reply_to=SimpleNamespace(reply_to_top_id=topic, reply_to_msg_id=query["msg_id"] if reply is None else reply),
    )


async def check_quotas(env, counts):
    for used in counts:
        await hunt_weekly.scheduler(env.ctx, env.now[0])
        assert env.sends[-1][0] == ".船况"
        assert await hunt_weekly.game_event(env.ctx, receipt(env, used))
    await hunt_weekly.scheduler(env.ctx, env.now[0])


def test_missing_quota_never_opens_a_voyage(weekly_env):
    env = weekly_env
    async def run():
        await hunt_weekly.scheduler(env.ctx, env.now[0])
        await hunt_weekly.scheduler(env.ctx, env.now[0] + 1)
    asyncio.run(run())
    assert [command for command, _ in env.sends] == [".船况"]
    assert not env.runs


@pytest.mark.parametrize("counts,expected", [([5, 5], "done"), ([2, 2], "running"), ([5, 3], "running")])
def test_only_server_quotas_authorize_weekly_start_or_completion(weekly_env, counts, expected):
    env = weekly_env
    asyncio.run(check_quotas(env, counts))
    assert env.weekly["周常队"]["phase"] == expected
    assert sum(command == ".出海猎妖" for command, _ in env.sends) == (expected == "running")


@pytest.mark.parametrize("changes", [{"reply": 999}, {"sender": 98}, {"chat": -200}, {"topic": 400}])
def test_foreign_status_receipts_cannot_supply_quota(weekly_env, changes):
    env = weekly_env
    async def run():
        await hunt_weekly.scheduler(env.ctx, env.now[0])
        assert not await hunt_weekly.game_event(env.ctx, receipt(env, 0, **changes))
    asyncio.run(run())
    assert not env.weekly["周常队"]["counters"]


def test_duplicate_quota_receipt_does_not_satisfy_the_next_member(weekly_env):
    env = weekly_env
    async def run():
        await hunt_weekly.scheduler(env.ctx, env.now[0])
        first = receipt(env, 2)
        assert await hunt_weekly.game_event(env.ctx, first)
        await hunt_weekly.scheduler(env.ctx, env.now[0])
        assert not await hunt_weekly.game_event(env.ctx, first)
    asyncio.run(run())
    assert set(env.weekly["周常队"]["counters"]) == {"1"}
    assert not env.runs


def test_next_week_rechecks_actual_counter_without_assuming_it_reset(weekly_env):
    env = weekly_env
    async def run():
        await check_quotas(env, [5, 5])
        env.now[0] = hunt_weekly.next_week_check(env.now[0]) + 1
        await check_quotas(env, [5, 5])
    asyncio.run(run())
    assert [command for command, _ in env.sends] == [".船况"] * 4
    assert not env.runs


def test_successful_return_requires_fresh_quotas_before_next_trip(weekly_env):
    env = weekly_env
    async def run():
        await check_quotas(env, [2, 2])
        run = env.runs["周常队"]
        run["phase"] = "done"
        await hunt_weekly.scheduler(env.ctx, env.now[0])
        assert not env.weekly["周常队"]["counters"]
        env.now[0] += hunt_weekly.BETWEEN_VOYAGES + 1
        await hunt_weekly.scheduler(env.ctx, env.now[0])
    asyncio.run(run())
    assert env.sends[-1][0] == ".船况"
    assert sum(command == ".出海猎妖" for command, _ in env.sends) == 1


def test_uncertain_open_is_held_and_cannot_be_repeated(weekly_env):
    env = weekly_env
    send = env.ctx.send
    async def uncertain(command, **kwargs):
        if command == ".出海猎妖":
            env.sends.append((command, kwargs))
            assert env.runs["周常队"]["command_attempts"]
            return None
        return await send(command, **kwargs)
    env.ctx.send = uncertain
    async def run():
        await check_quotas(env, [2, 2])
        env.now[0] += 3600
        await hunt_weekly.scheduler(env.ctx, env.now[0])
        assert not (await hunt.start(env.ctx, "周常队"))[0]
    asyncio.run(run())
    assert env.weekly["周常队"]["phase"] == "held"
    assert env.runs["周常队"]["requires_reconciliation"]
    assert sum(command == ".出海猎妖" for command, _ in env.sends) == 1


def test_unknown_weekly_format_holds_without_guessing(weekly_env):
    env = weekly_env
    async def run():
        await hunt_weekly.scheduler(env.ctx, env.now[0])
        await hunt_weekly.game_event(env.ctx, receipt(env, 0, text="本周有奖出海：未知"))
        await hunt_weekly.scheduler(env.ctx, env.now[0] + 600)
    asyncio.run(run())
    assert env.weekly["周常队"]["phase"] == "held"
    assert not env.runs


def test_weekly_option_is_validated_and_defaults_to_manual():
    team = {"name": "队伍", "member_identity_ids": [1, 2]}
    assert not hunt.normalize_teams([team], {1, 2}, lambda value: value)[0].get("auto_weekly")
    assert hunt.normalize_teams([{**team, "auto_weekly": True}], {1, 2}, lambda value: value)[0]["auto_weekly"]
    with pytest.raises(ValueError):
        hunt.normalize_teams([{**team, "auto_weekly": "true"}], {1, 2}, lambda value: value)


def test_stale_quota_is_requeried_before_opening(weekly_env):
    env = weekly_env
    async def run():
        await hunt_weekly.scheduler(env.ctx, env.now[0])
        await hunt_weekly.game_event(env.ctx, receipt(env, 2))
        env.now[0] += 181
        await hunt_weekly.scheduler(env.ctx, env.now[0])
    asyncio.run(run())
    assert env.weekly["周常队"]["query"]["identity_id"] == 1
    assert not env.weekly["周常队"]["counters"]
    assert not env.runs


def test_weekly_recheck_cannot_release_unknown_boat(weekly_env):
    env = weekly_env
    env.weekly["周常队"] = {"phase": "held"}
    env.runs["周常队"] = {"phase": "held", "requires_reconciliation": True}
    assert not asyncio.run(hunt_weekly.reset_check(env.ctx, "周常队"))[0]
    assert env.weekly["周常队"]["phase"] == "held"
    env.runs.clear()
    assert asyncio.run(hunt_weekly.reset_check(env.ctx, "周常队"))[0]
    assert env.weekly["周常队"]["phase"] == "idle"


@pytest.mark.parametrize("fresh,key,expected", [(True, "1000.0", True), (False, "1000.0", False), (True, "999", False)])
def test_operator_confirmation_keeps_receipts_and_forces_fresh_quotas(weekly_env, fresh, key, expected):
    env = weekly_env
    run = {"phase": "held", "started_at": 1000.0, "requires_reconciliation": True,
           "command_attempts": {"open": {"status": "unknown"}}}
    env.runs["周常队"] = run
    env.weekly["周常队"] = {"phase": "held", "counters": {"1": {"used": 2}}}
    event = SimpleNamespace(raw_text=".猎妖 确认结束 周常队 " + key,
                            date=datetime.fromtimestamp(env.now[0] - (0 if fresh else 90), timezone.utc))
    asyncio.run(hunt.control(env.ctx, event))
    assert (not run["requires_reconciliation"]) is expected
    assert run["command_attempts"]["open"]["status"] == "unknown"
    if expected:
        assert not env.weekly["周常队"]["counters"]
