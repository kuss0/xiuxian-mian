"""Real refusal wording, shared limits, and send races without live traffic."""
import asyncio
import copy
import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from model import dungeon_openings as limits, state


@pytest.fixture
def opening_env(monkeypatch):
    before = copy.deepcopy(state._meta_state)
    state.set_expedition_run_state({"weekly": {"quotas": {"yuejing": {"1": {"used": 1}}}}})
    now = [1794794300.0]
    monkeypatch.setattr(limits.time, "time", lambda: now[0])
    monkeypatch.setattr(limits, "get_game_group_ids", lambda: [-100, -200])
    monkeypatch.setattr(limits, "get_game_bot_ids", lambda: [99])
    monkeypatch.setattr(limits, "get_identity_ids", lambda: [1, 2, 3, 4, 5, 111])
    monkeypatch.setattr(limits, "get_avatar_owner_id", lambda i: 1 if i == 111 else 0)
    save = Mock(return_value=True)
    monkeypatch.setattr(limits, "save_state", save)
    monkeypatch.setattr(limits, "mark_dirty", Mock())
    monkeypatch.setattr(limits, "_LOADED", True)
    monkeypatch.setattr(limits, "_LOAD_LOCK", None)
    monkeypatch.setattr(limits, "_REPLAYING", False)
    monkeypatch.setattr(limits, "_EARLY", [])
    monkeypatch.setattr(limits, "is_game_send_definitely_unsent", lambda *_: False)
    monkeypatch.setattr(limits, "get_last_game_send_block", lambda *_: {"at": now[0]})
    yield SimpleNamespace(now=now, save=save)
    state._meta_state.clear()
    state._meta_state.update(before)


def event(env, text, *, sender=99, chat=-100, mid=200, reply=100, topic=42, at=None, forwarded=False):
    return SimpleNamespace(raw_text=text, sender_id=sender, chat_id=chat, id=mid, reply_to_msg_id=reply,
        topic_id=topic, forwarded=forwarded,
        date=datetime.fromtimestamp(env.now[0] if at is None else at, limits.TZ_LOCAL))


def command(env, text=".天澜草原", **kwargs):
    limits.observe(event(env, text, sender=kwargs.pop("sender", 1), mid=kwargs.pop("mid", 100), reply=0, **kwargs))


ACCEPTED = {
    ".出海猎妖": "【猎妖船】@member1 挂起猎妖旗，招募船员！90 秒内发 .登船 加入。",
    ".阴冥之地": "【阴冥之地】@member1 要入阴冥，.同入阴冥 加入。",
    ".天澜草原": "【天澜草原】@member1 要横穿天澜草原，.同行 加入。",
    ".夜探越京": "【越京夜行】@member1 要夜探越国京城，.同往 加入。",
    ".广寒界": "【广寒界·寻香】@member1 带队，.入界 同行。",
    ".魔金山脉": "【魔金山脉·猎魔猿】@member1 带队，.入山 同行。",
    ".开密议": "【七派密议】@member1 召集密议，.入议 加入。",
}


@pytest.mark.parametrize("opener", ACCEPTED)
def test_all_seven_openings_share_group_cooldown_across_topics(opening_env, opener):
    env = opening_env
    command(env, opener)
    accepted = event(env, ACCEPTED[opener])
    limits.observe(accepted)
    limits.observe(accepted)  # replay/edit must not consume another slot
    assert limits._records()["roots"]["1"]["used"] == 1
    assert limits._records()["groups"]["-100"]["used"] == 1
    assert not limits.ready(2, -100, env.now[0] + 600)
    assert limits.ready(2, -100, env.now[0] + 616)
    assert limits.ready(2, -200, env.now[0])
    assert state.get_expedition_run_state()["weekly"]["quotas"]["yuejing"]["1"]["used"] == 1


def test_two_different_dungeons_use_one_root_daily_budget_but_do_not_block_joining(opening_env):
    env = opening_env
    command(env)
    limits.observe(event(env, ACCEPTED[".天澜草原"]))
    env.now[0] += 616
    command(env, ".夜探越京", sender=111, mid=101)
    limits.observe(event(env, ACCEPTED[".夜探越京"], mid=201, reply=101))
    assert not limits.ready(1, -200)
    assert not limits.ready(111, -200)
    assert limits.ready(2, -200)
    env.now[0] = limits.next_day(env.now[0])
    assert limits.ready(1, -200)


@pytest.mark.parametrize("text,scope", [
    ("主魂今日发起次数已满（共 2 局），北京时间零点后再来。", "root"),
    ("本群今日发起次数已满（共 12 局），北京时间零点后再来。", "group"),
    ("本群开局冷却中，还需 9 分钟。", "cooldown"),
])
def test_refusals_do_not_consume_rewards_or_an_accepted_opening(opening_env, text, scope):
    env = opening_env
    command(env, ".出海猎妖")
    limits.observe(event(env, text))
    groups = limits._records()["groups"]["-100"]
    roots = limits._records()["roots"]["1"]
    assert groups["used"] == (12 if scope == "group" else 0)
    assert roots["used"] == (2 if scope == "root" else 0)
    assert not limits.ready(1, -100)
    assert limits.ready(2, -200)
    assert limits.ready(2, -100) is (scope == "root")
    assert state.get_expedition_run_state()["weekly"]["quotas"]["yuejing"]["1"]["used"] == 1


@pytest.mark.parametrize("changes", [{"sender": 98}, {"forwarded": True}, {"topic": 43}, {"at": 1794794290.0}, {"chat": -300}])
def test_untrusted_or_wrong_route_reply_cannot_change_limits(opening_env, changes):
    command(opening_env)
    before = copy.deepcopy(limits._records())
    limits.observe(event(opening_env, "主魂今日发起次数已满（共 2 局），北京时间零点后再来。", **changes))
    assert limits._records() == before


def test_other_players_openings_use_group_budget_but_not_our_root_budget(opening_env):
    env = opening_env
    command(env, sender=500)
    limits.observe(event(env, ACCEPTED[".天澜草原"]))
    assert not limits._records().get("roots")
    assert not limits.ready(1, -100)
    assert limits.ready(1, -200)


@pytest.mark.parametrize("early", ["accepted", "daily_limit"])
def test_early_reply_is_applied_after_send_returns_without_reopening(opening_env, early):
    env = opening_env
    async def sender(guard):
        assert guard()
        text = ACCEPTED[".天澜草原"] if early == "accepted" else "主魂今日发起次数已满（共 2 局），北京时间零点后再来。"
        limits.observe(event(env, text))
        return SimpleNamespace(id=100)
    result = asyncio.run(limits.send(".天澜草原", 1, -100, 42, "first", sender))
    assert result.id == 100
    assert limits._records()["roots"]["1"]["used"] == (1 if early == "accepted" else 2)
    assert not any(o["phase"] == "pending" for o in limits._records()["openings"].values())


def test_parallel_features_reserve_one_opening_before_awaiting_send(opening_env):
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()
        sent = []
        async def first_sender(guard):
            assert guard()
            sent.append(1)
            started.set()
            await finish.wait()
            return SimpleNamespace(id=100)
        first = asyncio.create_task(limits.send(".天澜草原", 1, -100, 42, "first", first_sender))
        await started.wait()
        async def second_sender(guard):
            assert not guard()
            return None
        assert await limits.send(".出海猎妖", 2, -100, 0, "second", second_sender) is None
        finish.set()
        await first
        assert sent == [1]
        assert not limits.ready(2, -100)  # send alone is not a confirmed result
    asyncio.run(run())


def test_save_failure_prevents_send_and_rolls_back_claim(opening_env):
    opening_env.save.return_value = False
    async def sender(_guard):
        pytest.fail("must not send after persistence failure")
    with pytest.raises(RuntimeError, match="保存失败"):
        asyncio.run(limits.send(".天澜草原", 1, -100, 42, "first", sender))
    assert not limits._records().get("openings")


@pytest.mark.parametrize("unsent", [True, False])
def test_only_definitely_unsent_result_releases_opening_claim(opening_env, monkeypatch, unsent):
    monkeypatch.setattr(limits, "is_game_send_definitely_unsent", lambda *_: unsent)
    async def sender(guard):
        assert guard()
        return None
    asyncio.run(limits.send(".天澜草原", 1, -100, 42, "first", sender))
    assert limits.ready(1, -100) is unsent


def test_old_unsent_evidence_cannot_release_a_new_unknown_send(opening_env, monkeypatch):
    monkeypatch.setattr(limits, "is_game_send_definitely_unsent", lambda *_: True)
    monkeypatch.setattr(limits, "get_last_game_send_block", lambda *_: {"at": opening_env.now[0] - 1})
    async def sender(_guard):
        return None
    asyncio.run(limits.send(".天澜草原", 1, -100, 42, "first", sender))
    assert not limits.ready(1, -100)


def test_own_command_echo_is_still_pending_until_game_reply(opening_env):
    async def sender(guard):
        assert guard()
        command(opening_env)
        return SimpleNamespace(id=100)
    asyncio.run(limits.send(".天澜草原", 1, -100, 42, "first", sender))
    assert not limits.ready(2, -100)
    limits.observe(event(opening_env, ACCEPTED[".天澜草原"]))
    assert limits._records()["roots"]["1"]["used"] == 1


def test_status_reply_with_recruitment_text_cannot_consume_an_opening(opening_env):
    limits.observe(event(opening_env, ACCEPTED[".天澜草原"]))
    assert not limits._records().get("groups")


def test_startup_local_replay_is_deduplicated_and_saved_once(opening_env, monkeypatch, tmp_path):
    env = opening_env
    records = []
    for e in (event(env, ".天澜草原", sender=1, mid=100, reply=0),
              event(env, ACCEPTED[".天澜草原"]),
              event(env, ".阴冥之地", sender=1, mid=101, reply=0),
              event(env, "主魂今日发起次数已满（共 2 局），北京时间零点后再来。", mid=201, reply=101)):
        records.append({"event_type": "message", "server_event_at": e.date.timestamp(), "message_id": e.id,
            "sender_id": e.sender_id, "chat_id": e.chat_id, "topic_id": e.topic_id,
            "reply_to_msg_id": e.reply_to_msg_id, "text": e.raw_text})
    (tmp_path / (limits.day(env.now[0]) + ".log")).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records * 2))
    monkeypatch.setattr(limits, "MESSAGES_DIR", tmp_path)
    monkeypatch.setattr(limits, "_LOADED", False)
    asyncio.run(limits.ensure_loaded())
    assert limits._records()["roots"]["1"]["used"] == 2
    assert limits._records()["groups"]["-100"]["used"] == 1
    env.save.assert_called_once()


def test_real_cross_dungeon_history_seeds_shared_daily_limits(opening_env, monkeypatch, tmp_path):
    rows = json.loads((Path(__file__).parent / "fixtures/dungeon_openings_20261116.json").read_text())
    opening_env.now[0] = 1794796000.0
    (tmp_path / "2026-11-16.log").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
    monkeypatch.setattr(limits, "MESSAGES_DIR", tmp_path)
    monkeypatch.setattr(limits, "_LOADED", False)
    asyncio.run(limits.ensure_loaded())
    roots = limits._records()["roots"]
    assert {key: roots[key]["used"] for key in ("1", "2", "3")} == {"1": 2, "2": 2, "3": 1}
    assert limits._records()["groups"]["-100"]["used"] == 6
    assert not limits.ready(1, -200)
    assert limits.ready(3, -200)
    assert not limits.ready(3, -100)
    opening_env.save.assert_called_once()
    asyncio.run(limits.ensure_loaded())
    opening_env.save.assert_called_once()


def test_manual_reconciliation_releases_only_its_original_pending_claim(opening_env):
    state.get_expedition_run_state()["weekly"]["runs"] = {"yuejing": {
        "kind": "yuejing", "id": "abc", "chat_id": -100, "phase": "operator_confirmed",
        "operator_confirmed_at": opening_env.now[0], "requires_reconciliation": False}}
    limits._records()["openings"] = {"pending": {"phase": "pending", "command": ".夜探越京",
        "root": 1, "chat_id": -100, "op_id": "weekly:yuejing:abc:0:1:.夜探越京"}}
    assert limits.ready(1, -100)
    limits._records()["openings"]["pending"]["op_id"] = "weekly:yuejing:other:0:1:.夜探越京"
    assert not limits.ready(1, -100)
