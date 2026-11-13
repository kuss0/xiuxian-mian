import asyncio
import copy
import random
import time
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from model import app_expeditions, app_hunt, app_xuangu, persistence
from model import state as state_module
from model.features import changfeng_trial as trial, hunt, xuangu
from model.features.expedition_store import FubenFlowStore


@pytest.fixture(autouse=True)
def isolated_state():
    before = copy.deepcopy(state_module._meta_state)
    state_module.set_hunt_run_state({})
    state_module.set_hunt_teams([])
    state_module.set_expedition_config({})
    state_module.set_expedition_run_state({})
    hunt._LOCKS.clear()
    xuangu._XUANGU_DISPATCHING.clear()
    yield
    state_module._meta_state.clear()
    state_module._meta_state.update(before)


def event(text, *, reply=0, chat=-100, topic=300, sender=99):
    return SimpleNamespace(raw_text=text, sender_id=sender, chat_id=chat, id=10000,
                           date=datetime.now(timezone.utc),
                           reply_to=SimpleNamespace(reply_to_msg_id=reply, reply_to_top_id=topic))


class HuntTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runs, self.sends = {}, []
        self.enabled = True
        self.members = [{"identity_id": 1, "root_id": 1, "username": "captain"},
                        {"identity_id": 2, "root_id": 2, "username": "mate"}]
        self.team = {"name": "本机队", "group_role": "secondary", "seas": ["近海", "暗礁", "外海"], "member_identity_ids": [1, 2]}

        async def send(command, **kwargs):
            if not kwargs["send_guard"]():
                return None
            self.sends.append((command, kwargs))
            return SimpleNamespace(id=len(self.sends) + 100)

        self.ctx = SimpleNamespace(teams=lambda: [self.team], runs=lambda: self.runs,
                                   save=lambda: None, target=lambda role: (-100, 300),
                                   members=lambda ids: (copy.deepcopy(self.members), ""), enabled=lambda: self.enabled,
                                   bot_ids=lambda: [99], send=send, notice=AsyncMock())

    async def recruit(self):
        ok, message = await hunt.start(self.ctx, "本机队")
        self.assertTrue(ok, message)
        await hunt.game_event(self.ctx, event("@captain 挂起猎妖旗，猎妖船开始招人", reply=101))
        await hunt.game_event(self.ctx, event("@mate 登船（2/5）", reply=102))
        self.assertEqual([".出海猎妖", ".登船", ".起锚"], [row[0] for row in self.sends])

    async def scout(self):
        await self.recruit()
        await hunt.game_event(self.ctx, event("【猎妖船】第1日，选海域\n船员：@captain @mate\n剩余 30 秒"))
        await hunt.game_event(self.ctx, event("【猎妖船】第1日·风浪\n船员：@captain @mate\n征兆：未知\n剩余 45 秒"))
        self.assertEqual(".探路", self.sends[-1][0])

    async def test_confirmed_route_scout_and_terminal_receipt(self):
        await self.scout()
        scout_id = len(self.sends) + 100
        await hunt.game_event(self.ctx, event("你攀上桅杆看清了：是【妖兽群】", reply=scout_id))
        self.assertEqual([".出手", ".出手"], [row[0] for row in self.sends[-2:]])
        self.assertEqual([], self.runs["本机队"]["action_confirmed"])
        await hunt.game_event(self.ctx, event("你选择【出手】", reply=len(self.sends) + 100))
        self.assertEqual([1], self.runs["本机队"]["action_confirmed"])
        before = len(self.sends)
        await hunt.game_event(self.ctx, event("你攀上桅杆看清了：是【妖兽群】", reply=scout_id), event_type="edit")
        self.assertEqual(before, len(self.sends))
        await hunt.game_event(self.ctx, event("【猎妖船·返航】@captain @mate 灵石+5"))
        self.assertEqual("done", self.runs["本机队"]["phase"])
        await hunt.scheduler(self.ctx, time.time() + 10000)
        self.assertEqual(before, len(self.sends))

    async def test_captain_vote_keeps_route_and_ignores_duplicate_panel(self):
        await self.recruit()
        panel = "【猎妖船】第1日，选海域\n船员：@captain @mate\n剩余 30 秒"
        before = len(self.sends)
        await hunt.game_event(self.ctx, event(panel))
        await hunt.game_event(self.ctx, event(panel), event_type="edit")
        votes = self.sends[before:]
        self.assertEqual([(".航向 近海", 1)],
                         [(command, kwargs["send_as_id"]) for command, kwargs in votes])

    async def test_outer_preference_uses_each_owned_day_hull_and_ignores_foreign_panel(self):
        self.team["seas"] = ["外海"] * 3
        await self.recruit()
        before = len(self.sends)
        foreign = "【猎妖船】第1日，选海域\n船体 █ 1/100　舱中收获 0.0\n船员：@other @mate\n剩余 30 秒"
        await hunt.game_event(self.ctx, event(foreign))
        self.assertEqual(before, len(self.sends))
        for day, hull in enumerate((100, 65, 30), 1):
            panel = f"【猎妖船】第{day}日，选海域\n船体 █ {hull}/100　舱中收获 0.0\n船员：@captain @mate\n剩余 30 秒"
            await hunt.game_event(self.ctx, event(panel))
            await hunt.game_event(self.ctx, event(panel), event_type="edit")
        self.assertEqual([".航向 外海", ".航向 外海", ".航向 近海"],
                         [command for command, _ in self.sends[before:]])
        decisions = self.runs["本机队"]["route_decisions"]
        self.assertEqual([100, 65, 30], [decisions[str(day)]["hull"] for day in (1, 2, 3)])

    async def test_outer_preference_never_uses_old_hull_when_new_panel_is_missing_it(self):
        self.team["seas"] = ["外海"] * 3
        await self.recruit()
        await hunt.game_event(self.ctx, event("【猎妖船】第1日，选海域\n船体 █ 100/100　舱中收获 0.0\n船员：@captain @mate\n剩余 30 秒"))
        before = len(self.sends)
        await hunt.game_event(self.ctx, event("【猎妖船】第2日，选海域\n船员：@captain @mate\n剩余 30 秒"))
        self.assertEqual(before, len(self.sends))
        self.assertEqual("held", self.runs["本机队"]["phase"])
        self.assertTrue(self.runs["本机队"]["requires_reconciliation"])

    async def test_two_player_combat_fits_paced_send_window(self):
        await self.recruit()
        now = [time.time()]
        last_sent = [now[0] - 20]
        blocked = []

        async def paced_send(command, **kwargs):
            # Observed live sends need about 15 seconds plus acknowledgement time.
            # Completing every vote early can start the 45-second sea window
            # while the scout is still waiting behind the last vote.
            now[0] = max(now[0], last_sent[0] + 17)
            if not kwargs["send_guard"]():
                blocked.append((command, kwargs["send_as_id"]))
                return None
            self.sends.append((command, kwargs))
            last_sent[0] = now[0]
            now[0] += 4
            return SimpleNamespace(id=len(self.sends) + 100)

        def at_current_time(text, **kwargs):
            sample = event(text, **kwargs)
            sample.date = datetime.fromtimestamp(now[0], timezone.utc)
            return sample

        self.ctx.send = paced_send
        voting_started = now[0]
        before = len(self.sends)
        with patch.object(hunt.time, "time", side_effect=lambda: now[0]):
            await hunt.game_event(self.ctx, at_current_time(
                "【猎妖船】第1日，选海域\n船员：@captain @mate\n剩余 30 秒"))
            votes = [row for row in self.sends[before:] if row[0].startswith(".航向 ")]
            if len(votes) < len(self.members):
                now[0] = max(now[0], voting_started + 30)
            await hunt.game_event(self.ctx, at_current_time(
                "【猎妖船】第1日·风浪\n船员：@captain @mate\n征兆：未知\n剩余 45 秒"))
            scout_id = len(self.sends) + 100
            await hunt.game_event(self.ctx, at_current_time(
                "你攀上桅杆看清了：是【妖兽群】", reply=scout_id))
        self.assertEqual([], blocked)
        run = self.runs["本机队"]
        self.assertEqual({1, 2}, set(run["action_sent"]))
        self.assertEqual([], run["action_failed"])
        self.assertEqual([2, 1], [kwargs["send_as_id"] for command, kwargs in self.sends if command == ".出手"])

    async def test_unknown_reveal_holds_until_explicit_action(self):
        await self.scout()
        await hunt.game_event(self.ctx, event("你攀上桅杆看清了：是【新海况】", reply=len(self.sends) + 100))
        self.assertEqual("awaiting_action", self.runs["本机队"]["phase"])
        before = len(self.sends)
        await hunt.scheduler(self.ctx, self.runs["本机队"]["deadline"] + 1)
        self.assertEqual(before, len(self.sends))

    async def test_wrong_bot_chat_topic_and_other_captain_do_not_send(self):
        await self.recruit()
        before = len(self.sends)
        text = "【猎妖船】第1日，选海域\n船员：@captain @mate\n剩余30秒"
        for kwargs in ({"sender": 98}, {"chat": -200}, {"topic": 400}):
            await hunt.game_event(self.ctx, event(text, **kwargs))
        await hunt.game_event(self.ctx, event(text.replace("@captain", "@other")))
        self.assertEqual(before, len(self.sends))

    async def test_stop_invalidates_queued_operation(self):
        await self.recruit()
        run = self.runs["本机队"]
        guards = []

        async def delayed(command, **kwargs):
            guards.append(kwargs["send_guard"])
            run["stop_requested"] = True
            self.assertFalse(kwargs["send_guard"]())
            return None

        self.ctx.send = delayed
        before = len(self.sends)
        await hunt._send(self.ctx, run, run["members"][0], ".探路")
        self.assertEqual(before, len(self.sends))
        self.assertFalse(guards[0]())

    async def test_adapter_keeps_local_throttle_and_pins_chat(self):
        with patch.object(app_hunt, "hunt_target", return_value=(-200, 300)), \
                patch.object(app_hunt, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=1))) as send:
            await app_hunt._send_hunt_command(".探路", send_as_id=1, target_group_role="secondary", send_guard=lambda: True,
                                              skip_throttle=True)
        kwargs = send.await_args.kwargs
        self.assertEqual(-200, kwargs["target_chat_id"])
        self.assertNotIn("skip_throttle", kwargs)
        self.assertEqual(0, kwargs["max_retry"])
        self.assertTrue(kwargs["operation_check"]())

    async def test_live_17_then_24_second_queue_delay_no_longer_drops_captain(self):
        await self.recruit()
        now = [time.time()]
        run = self.runs["本机队"]
        run.update(phase="voting", day=3)
        last_identity = {}
        blocked = []

        async def transport(command, **kwargs):
            identity = kwargs["send_as_id"]
            if command == ".探路":
                delay = 2
            elif kwargs["priority"] == "urgent_reactive":
                delay = 3
            else:
                delay = 17 if identity == 2 else 24
            now[0] = max(now[0] + delay, last_identity.get(identity, 0) + 10)
            if not kwargs["operation_check"]():
                blocked.append(command)
                return None
            self.sends.append((command, kwargs))
            last_identity[identity] = now[0]
            now[0] += 1
            return SimpleNamespace(id=len(self.sends) + 100)

        self.ctx.send = app_hunt._send_hunt_command
        def at_current_time(text, **kwargs):
            sample = event(text, **kwargs)
            sample.date = datetime.fromtimestamp(now[0], timezone.utc)
            return sample

        with patch.object(hunt.time, "time", side_effect=lambda: now[0]), \
                patch.object(app_hunt, "hunt_target", return_value=(-100, 300)), \
                patch.object(app_hunt, "send_game_command", side_effect=transport):
            await hunt.game_event(self.ctx, at_current_time(
                "【猎妖船】第3日 · 外海　征兆：罗盘乱转\n船体 █ 100/100　舱中收获 4.8\n船员：@captain @mate\n剩余 45 秒"))
            scout_id = len(self.sends) + 100
            await hunt.game_event(self.ctx, at_current_time(
                "你攀上桅杆看清了：是【磁山崩落】。", reply=scout_id))
        self.assertEqual([], blocked)
        self.assertEqual([2, 1], run["action_sent"])
        self.assertLess(now[0], run["deadline"])
        self.assertTrue(all(kwargs["priority"] == "urgent_reactive" for _, kwargs in self.sends[-3:]))
        self.assertTrue(all("skip_throttle" not in kwargs for _, kwargs in self.sends[-3:]))

    async def test_hunt_deadline_is_checked_again_inside_send_queue(self):
        now = [time.time()]
        with patch.object(app_hunt.time, "time", side_effect=lambda: now[0]), \
                patch.object(app_hunt, "hunt_target", return_value=(-100, 300)), \
                patch.object(app_hunt, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=1))) as send:
            await app_hunt._send_hunt_command(".护船", send_as_id=1, target_group_role="secondary",
                                              send_guard=lambda: True, deadline=now[0] + 5)
            kwargs = send.await_args.kwargs
            self.assertEqual("urgent_reactive", kwargs["priority"])
            self.assertLessEqual(kwargs["queue_timeout"], 5)
            self.assertTrue(kwargs["operation_check"]())
            now[0] += 6
            self.assertFalse(kwargs["operation_check"]())


class LongwindProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.calls, self.waits, self.saved = [], [], []
        self.record = {"daily_used": 0}
        self.now = 0.0

    async def sleeper(self, delay):
        self.waits.append(delay)
        self.now += delay

    def save(self):
        self.saved.append(copy.deepcopy(self.record))

    async def call(self, action, **payload):
        self.calls.append((action, payload))
        if action == "status":
            return {"status": {"carryAvailable": 0, "remaining": 0, "tier": {"next": 2}}}
        if action == "start":
            return {"run": {"id": "run1", "rewarded": True, "tier": 2}}
        return {"grants": [{"name": "灵石", "quantity": 2}], "result": {"grade": "乙"}}

    async def run_trial(self, call=None):
        return await trial.run_protocol(call or self.call, "longwind", self.record, self.save,
                                        sleeper=self.sleeper, clock=lambda: self.now, rng=random.Random(3))

    async def test_zero_carry_and_rewarded_start_override_stale_remaining(self):
        await self.run_trial()
        start_payload = next(payload for action, payload in self.calls if action == "start")
        self.assertEqual(0, start_payload["carry"])
        self.assertEqual(4, len([action for action, payload in self.calls if action == "checkpoint"]))
        self.assertEqual("done", self.record["phase"])
        self.assertEqual(1, self.record["daily_used"])
        self.assertEqual({"灵石": 8}, self.record["gains"])

    async def test_pending_wait_honors_full_delay_and_same_wave_and_report(self):
        first = True

        async def call(action, **payload):
            nonlocal first
            result = await self.call(action, **payload)
            if action == "checkpoint" and first:
                first = False
                return {"pending": True, "retryAfter": 35}
            return result

        await self.run_trial(call)
        waves = [payload for action, payload in self.calls if action == "checkpoint"]
        self.assertGreaterEqual(self.waits[0], 35)
        self.assertEqual((0, 0, 1, 2, 3), tuple(payload["wave"] for payload in waves))
        self.assertEqual(waves[0]["report"], waves[1]["report"])

    async def test_unconfirmed_start_is_not_retried_or_ended(self):
        async def call(action, **payload):
            await self.call(action, **payload)
            if action == "start":
                raise trial.TrialError("trial_request_unconfirmed")
            return {"status": {"tier": {"next": 1}}}

        with self.assertRaisesRegex(trial.TrialError, "unconfirmed"):
            await self.run_trial(call)
        self.assertEqual(["status", "start"], [action for action, payload in self.calls])
        self.assertEqual("starting", self.saved[-1]["phase"])

    async def test_practice_is_closed_without_checkpoint_or_consumed_local_slot(self):
        async def call(action, **payload):
            result = await self.call(action, **payload)
            return {"run": {"id": "practice", "rewarded": False}} if action == "start" else result

        await self.run_trial(call)
        self.assertEqual(["status", "start", "end"], [action for action, payload in self.calls])
        self.assertEqual(0, self.record["daily_used"])

    async def test_grade_mismatch_stops_before_next_wave(self):
        async def call(action, **payload):
            result = await self.call(action, **payload)
            return {"result": {"grade": "甲上"}} if action == "checkpoint" else result

        with self.assertRaisesRegex(trial.TrialError, "verification_mismatch"):
            await self.run_trial(call)
        self.assertEqual(1, sum(action == "checkpoint" for action, payload in self.calls))


@pytest.mark.parametrize("value,expected", [(0, 0), (None, 3), (1, 1), (-1, 0), (100, 3), ("bad", 0), (float("nan"), 0)])
def test_carry_preserves_zero(value, expected):
    assert trial._trial_carry({"carryAvailable": value}) == expected


def test_serialized_reports_remain_conservative_and_varied():
    reports = [trial._trial_report("clean", wave % 4, 0) for wave in range(100)]
    assert all(55 <= trial.report_score(report) < 75 for report in reports)
    assert all(report["carryLeft"] == 0 for report in reports)
    assert len({tuple(report["metrics"].values()) for report in reports}) > 10


def roster(room="abcd", captain="captain", **changes):
    return {"flow_id": captain, "roster_id": captain, "room_id": room, "leader_username": captain,
            "phase": "running", "game_chat_id": -100, "game_topic_id": 300,
            "expires_at": time.time() + 1000, **changes}


def xuangu_context(rosters):
    runtime = {"xuangu_auto": {"rosters": rosters}}
    store = FubenFlowStore(lambda: runtime, lambda value: None)
    return SimpleNamespace(flows=store.collection("xuangu_auto", "rosters"),
                           messages=SimpleNamespace(game_command=AsyncMock(), audit=AsyncMock(), notice_by_chat=AsyncMock()),
                           identities=SimpleNamespace(participation_enabled=lambda *_: True),
                           commands=SimpleNamespace(disband=".解散玄骨"))


@pytest.mark.parametrize("changes", [{"chat": -200}, {"topic": 500}])
def test_xuangu_wrong_chat_or_topic_cannot_claim_room(changes):
    ctx = xuangu_context({"captain": roster()})
    assert xuangu._match_roster_for_round(ctx, "abcd", {"roles": {"探禁": "captain"}}, time.time(), event=event("", **changes)) is None


def test_xuangu_explicit_wrong_room_never_falls_back_to_captain():
    ctx = xuangu_context({"captain": roster()})
    assert xuangu._match_roster_for_round(ctx, "dead", {"roles": {"探禁": "captain"}}, time.time(), event=event("")) is None


def test_xuangu_ambiguous_roomless_panel_is_ignored():
    ctx = xuangu_context({"a": roster("a"), "b": roster("b")})
    assert xuangu._match_roster_for_round(ctx, "", {"roles": {"探禁": "captain"}}, time.time(), event=event("")) is None


def test_xuangu_old_room_message_cannot_claim_fresh_roster():
    item = roster(seen_at=time.time())
    message = event("房间 abcd")
    message.date = datetime.fromtimestamp(time.time() - 100, timezone.utc)
    assert not xuangu._roster_matches_event(item, message)
    assert xuangu.parse_xuangu_outcome("【玄骨遗府·收拢遗宝】") == "收拢遗宝"


class XuanguSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_mutation_is_recorded_before_send_and_never_repeated(self):
        item = roster(my_members=[{"identity_id": 1, "username": "captain"}], current_round=1)
        ctx = xuangu_context({"captain": item})

        async def unknown(command, **kwargs):
            self.assertIn("1:.玄骨行动 1 护队", item["command_attempts"])
            return None

        ctx.messages.game_command = AsyncMock(side_effect=unknown)
        await xuangu._send_xuangu_command(ctx, ".玄骨行动 1 护队", send_as_id=1)
        await xuangu._send_xuangu_command(ctx, ".玄骨行动 1 护队", send_as_id=1)
        self.assertEqual(1, ctx.messages.game_command.await_count)
        self.assertEqual("unknown", item["command_attempts"]["1:.玄骨行动 1 护队"]["status"])

    async def test_round_receipt_and_finish_require_real_terminal_outcome(self):
        item = roster(my_members=[{"identity_id": 1, "username": "captain"}], current_round=1,
                      is_captain=True, leader_identity_id=1)
        ctx = xuangu_context({"captain": item})
        ctx.commands.act, ctx.commands.finish = ".玄骨行动", ".玄骨收尾"
        ctx.finish_choice = "收拢遗宝"
        ctx.sleep = AsyncMock()
        ctx.messages.game_command.return_value = SimpleNamespace(id=123)
        panel = {"round": 1, "roles": {"探禁": "captain"}, "actions": ["验骨", "护队"], "soul": 80}
        message = event("房间 abcd")
        await xuangu._on_xuangu_round(ctx, message, panel, time.time())
        self.assertEqual("pending", item["round_actions"]["1"]["captain"]["status"])
        self.assertNotIn(1, item.get("round_done", []))
        await xuangu._on_xuangu_round(ctx, message, panel, time.time())
        self.assertEqual(1, ctx.messages.game_command.await_count)
        message.reply_to_msg_id = 123
        await xuangu._on_xuangu_action_reply(ctx, message, "已提交第1轮【验骨】", time.time())
        self.assertEqual([1], item["round_done"])
        message.raw_text = "房间 abcd\n收拢遗宝（可选）\n立即撤离（可选）"
        await xuangu._on_xuangu_outcome(ctx, message, "残图异动", time.time())
        self.assertEqual("finishing", item["phase"])
        self.assertEqual("收拢遗宝", item["finish_choice"])
        self.assertEqual(".玄骨收尾 收拢遗宝", ctx.messages.game_command.call_args.args[0])
        await xuangu._on_xuangu_outcome(ctx, message, "残图异动", time.time())
        self.assertEqual(2, ctx.messages.game_command.await_count)
        await xuangu._on_xuangu_outcome(ctx, message, "一线生机", time.time())
        self.assertEqual("settled", item["phase"])

    async def test_sent_or_unknown_join_is_not_blindly_repeated(self):
        item = roster(phase="joining", my_members=[{"identity_id": 2, "username": "mate"}],
                      room_seen_at=time.time(), join_attempts={"mate": {"count": 1, "last_at": time.time() - 30}})
        ctx = xuangu_context({"captain": item})
        await xuangu._retry_xuangu_joins(ctx, item, time.time())
        ctx.messages.game_command.assert_not_called()

    async def test_queued_old_round_is_invalidated(self):
        item = roster(my_members=[{"identity_id": 1}], current_round=1)
        ctx = xuangu_context({"captain": item})

        async def send(command, **kwargs):
            self.assertTrue(kwargs["send_guard"]())
            item["current_round"] = 2
            self.assertFalse(kwargs["send_guard"]())
            return None

        ctx.messages.game_command = send
        await xuangu._send_xuangu_command(ctx, ".玄骨行动 1 防守", send_as_id=1)


def test_defaults_do_not_enable_or_create_teams():
    snapshot = app_expeditions.get_expedition_snapshot()
    assert snapshot["hunt_teams"] == []
    assert snapshot["xuangu"]["enabled"] is False
    assert snapshot["changfeng"]["enabled"] is False


def test_config_validation_is_atomic_and_partial_updates_preserve_others():
    state_module.ensure_identity_registered(1)
    state_module.ensure_identity_registered(2)
    teams = [{"name": "本机队", "group_role": "primary", "seas": ["近海"] * 3, "member_identity_ids": [1, 2]}]
    with patch.object(app_expeditions, "save_state", return_value=True):
        assert app_expeditions.ui_set_expedition_config({"hunt_teams": teams})[0]
        before = state_module.get_hunt_teams()
        assert not app_expeditions.ui_set_expedition_config({"hunt_teams": [], "changfeng": {"daily_limit": 0}})[0]
        assert state_module.get_hunt_teams() == before
        assert app_expeditions.ui_set_expedition_config({"changfeng": {"enabled": False, "participant_identity_ids": [1]}})[0]
        assert state_module.get_hunt_teams() == before


def test_restart_holds_incomplete_work_without_resuming_sends():
    state_module.set_hunt_run_state({"boat": {"phase": "scouting"}})
    state_module.set_expedition_run_state({"xuangu_auto": {"rosters": {"captain": roster()}},
                                          "changfeng": {"1": {"phase": "checkpoint", "run_id": "old"}}})
    with patch.object(app_hunt, "send_game_command") as hunting_send, patch.object(app_xuangu, "send_game_command") as dungeon_send:
        app_expeditions.reset_expedition_runtime_on_startup()
        hunting_send.assert_not_called()
        dungeon_send.assert_not_called()
    assert state_module.get_hunt_run_state()["boat"]["phase"] == "interrupted"
    assert state_module.get_expedition_run_state()["xuangu_auto"]["rosters"]["captain"]["phase"] == "interrupted"
    assert state_module.get_expedition_run_state()["changfeng"]["1"]["phase"] == "held"


def test_persistence_codecs_round_trip_all_new_state():
    state_module.set_hunt_teams([{"name": "队伍"}])
    state_module.set_hunt_run_state({"boat": {"phase": "interrupted"}})
    state_module.set_expedition_config({"xuangu": {"enabled": False}})
    state_module.set_expedition_run_state({"changfeng": {"1": {"phase": "held"}}})
    for key in ("hunt_teams", "hunt_run_state", "expedition_config", "expedition_run_state"):
        getter, encode, decode = persistence._META_STATE_CODEC[key]
        original = copy.deepcopy(getter())
        encoded = encode(original)
        decode(encoded)
        assert getter() == original
