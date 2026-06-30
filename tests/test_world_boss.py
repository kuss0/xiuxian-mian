import asyncio
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import module_manifest
from model import state as state_module
from model.features import world_boss


OPEN_TEXT = (
    "━━━━━━━━━━━━━━━\n"
    "【世界通告｜真仙试锋开启】\n"
    "━━━━━━━━━━━━━━━\n"
    "万魂幡主·阴罗真影 青元子 降下阴罗法身。\n\n"
    "参战指令\n"
    "- .世界boss\n"
    "- .讨伐青元子 强攻 压低血线\n"
    "- .讨伐青元子 破幡 拆万魂幡护体\n"
    "- .讨伐青元子 镇魂 压制魔压\n"
    "- .讨伐青元子 护阵 稳住天机阵势\n"
)

STATUS_TEXT = (
    "【真仙试锋 · 青元子】\n"
    "法身：万魂幡主·阴罗真影\n"
    "阶段：第一阶段·万火归源\n"
    "血量：[██████████████████] 100%\n"
    "剩余：162.39亿亿 / 162.39亿亿\n"
    "幡魂：9 层 ｜ 破幡进度：0\n"
    "魔压：20/100 ｜ 阵势：100/120 ｜ 剩余时间：29分57秒\n"
)

DANGEROUS_PHASE2_STATUS = (
    "【真仙试锋 · 青元子】\n"
    "法身：万魂幡主·阴罗真影\n"
    "阶段：第二阶段·斩灵压顶\n"
    "血量：[█████████████░░░░░] 72%\n"
    "剩余：117.28亿亿 / 162.39亿亿\n"
    "幡魂：0 层 ｜ 破幡进度：24\n"
    "魔压：89/100 ｜ 阵势：21/120 ｜ 剩余时间：17分35秒\n"
    "你的出手：4/5\n"
)

SAFE_PHASE2_STATUS = (
    "【真仙试锋 · 青元子】\n"
    "法身：万魂幡主·阴罗真影\n"
    "阶段：第二阶段·斩灵压顶\n"
    "血量：[██████████████░░░░] 76%\n"
    "剩余：105.03亿亿 / 138.03亿亿\n"
    "幡魂：0 层 ｜ 破幡进度：0\n"
    "魔压：13/100 ｜ 阵势：99/120 ｜ 剩余时间：27分54秒\n"
)

SUPPRESS_REPLY = (
    "【讨伐青元子】\n"
    "你镇住阴魂回潮，魔压 -3。\n"
    "【青元反击·神识威压·裂魂针雨】｜魔压 +3｜阵势 -1｜你被真仙余威震伤，修为 -2827。\n"
    "当前：[█████████████░░░░░] 72% ｜ 幡魂 0 ｜ 魔压 89/100 ｜ 阵势 21/120"
)

GUARD_REPLY = (
    "【讨伐青元子】\n"
    "你护住天机阵势，阵势 +4。\n"
    "【青元反击·七焰扇·万火归源】｜魔压 +2｜你被真仙余威震伤，修为 -1854。\n"
    "当前：[██████████████████] 100% ｜ 幡魂 9 ｜ 魔压 38/100 ｜ 阵势 92/120"
)

BREAK_REPLY = (
    "【讨伐青元子】\n"
    "你斩落幡魂一角，推进破幡进度。\n"
    "【青元反击·七焰扇·万火归源】｜魔压 +2｜你被真仙余威震伤，修为 -1854。\n"
    "当前：[██████████████████] 100% ｜ 幡魂 8 ｜ 魔压 38/100 ｜ 阵势 92/120"
)

ATTACK_REPLY = (
    "【讨伐青元子】\n"
    "你祭出攻势，造成 3.30亿亿 伤害。\n"
    "【青元反击·阴罗本幡·拘魂索命】｜魔压 +2｜阵势 -3｜你被真仙余威震伤，修为 -274。\n"
    "当前：[█████████████░░░░░] 70% ｜ 幡魂 0 ｜ 魔压 90/100 ｜ 阵势 16/120"
)

CONCLUSION_TEXT = (
    "━━━━━━━━━━━━━━━\n"
    "【世界通告｜真仙试锋败退】\n"
    "━━━━━━━━━━━━━━━\n"
    "魔压冲破百限，阴罗煞潮反卷全场。\n\n"
    "战果\n"
    "- 结果：天道败退\n"
    "- 参战：66 人\n"
)


class WorldBossTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module.set_world_boss_run_state({})

    def tearDown(self):
        task = getattr(world_boss, "_WORLD_BOSS_ROUND_TASK", None)
        if task is not None and not task.done():
            task.cancel()
        world_boss._WORLD_BOSS_ROUND_TASK = None
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _register(self, identity_id, *, label="", world_boss_enabled=True):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username=label or str(identity_id), label=label or str(identity_id), enabled=True)
        identity_state = state_module.get_identity_state(identity_id)
        identity_state["world_boss_enabled"] = bool(world_boss_enabled)
        return identity_state

    async def _await_world_boss_round_task(self):
        task = getattr(world_boss, "_WORLD_BOSS_ROUND_TASK", None)
        if task is None:
            return
        await asyncio.wait_for(task, timeout=1.0)
        if getattr(world_boss, "_WORLD_BOSS_ROUND_TASK", None) is task:
            world_boss._WORLD_BOSS_ROUND_TASK = None

    def test_parse_real_world_boss_text_shapes(self):
        opened = world_boss.parse_world_boss_text(OPEN_TEXT, now=1_781_318_000.0)
        status = world_boss.parse_world_boss_text(STATUS_TEXT, now=1_781_318_000.0)
        suppress = world_boss.parse_world_boss_text(SUPPRESS_REPLY, now=1_781_318_000.0)
        guard = world_boss.parse_world_boss_text(GUARD_REPLY, now=1_781_318_000.0)
        break_flag = world_boss.parse_world_boss_text(BREAK_REPLY, now=1_781_318_000.0)
        attack = world_boss.parse_world_boss_text(ATTACK_REPLY, now=1_781_318_000.0)
        exhausted = world_boss.parse_world_boss_text("你本期真仙试锋出手已尽。（5/5）")
        inactive = world_boss.parse_world_boss_text("当前没有进行中的【真仙试锋】。")
        conclusion = world_boss.parse_world_boss_text(CONCLUSION_TEXT)

        self.assertEqual("open", opened["type"])
        self.assertEqual("status", status["type"])
        self.assertEqual("第一阶段·万火归源", status["phase"])
        self.assertEqual(100, status["hp_percent"])
        self.assertEqual(9, status["fanhun"])
        self.assertEqual(0, status["break_progress"])
        self.assertEqual(20, status["moya"])
        self.assertEqual(100, status["zhen"])
        self.assertEqual(29 * 60 + 57, status["remaining_sec"])
        self.assertEqual("镇魂", suppress["action"])
        self.assertEqual(89, suppress["moya"])
        self.assertEqual("护阵", guard["action"])
        self.assertEqual("破幡", break_flag["action"])
        self.assertEqual("强攻", attack["action"])
        self.assertEqual("3.30亿亿", attack["damage"])
        self.assertEqual("exhausted", exhausted["type"])
        self.assertEqual(5, exhausted["own_actions"])
        self.assertEqual("inactive", inactive["type"])
        self.assertEqual("conclusion", conclusion["type"])
        self.assertEqual("败退", conclusion["result"])
        self.assertEqual(66, conclusion["participants"])
        self.assertIsNone(world_boss.parse_world_boss_text("荣誉称号: 【真仙试锋】"))

    def test_dangerous_phase_two_blocks_strong_attack_and_rescues_closest_failure_edge(self):
        now = 1_781_318_607.0
        run_state = world_boss._blank_run_state(now)
        run_state["active"] = True
        world_boss._update_run_metrics(
            run_state,
            world_boss.parse_world_boss_text(DANGEROUS_PHASE2_STATUS, now=now),
            now,
        )
        identity_state = {"world_boss_action_count": 4, "world_boss_action_limit": 5, "world_boss_attack_count": 0}

        action = world_boss.choose_world_boss_action(8659059191, identity_state, run_state, now=now)

        self.assertEqual("镇魂", action)

    def test_rescue_thresholds_start_at_moya_eighty_and_zhen_ten(self):
        now = 1_781_318_610.0
        run_state = world_boss._blank_run_state(now)
        run_state.update(
            {
                "active": True,
                "last_status_at": now,
                "phase": "第一阶段·万火归源",
                "hp_percent": 100,
                "moya": 80,
                "zhen": 10,
                "summary": {"镇魂": 0, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )
        identity_state = {"world_boss_action_count": 0, "world_boss_action_limit": 5, "world_boss_attack_count": 0}

        self.assertEqual("护阵", world_boss.choose_world_boss_action(3907536807, identity_state, run_state, now=now))

    def test_safe_phase_two_allows_only_named_strong_attackers(self):
        now = 1_781_318_800.0
        run_state = world_boss._blank_run_state(now)
        run_state.update(
            {
                "active": True,
                "last_status_at": now,
                "phase": "第二阶段·斩灵压顶",
                "hp_percent": 70,
                "moya": 55,
                "zhen": 90,
            }
        )
        wa_state = {"world_boss_action_count": 0, "world_boss_action_limit": 5, "world_boss_attack_count": 0}
        normal_state = {"world_boss_action_count": 0, "world_boss_action_limit": 5, "world_boss_attack_count": 0}

        self.assertEqual("强攻", world_boss.choose_world_boss_action(8659059191, wa_state, run_state, now=now))
        self.assertEqual("强攻", world_boss.choose_world_boss_action(301299112, wa_state, run_state, now=now))
        self.assertIn(world_boss.choose_world_boss_action(123456789, normal_state, run_state, now=now), {"镇魂", "护阵"})

    def test_maintenance_prefers_suppress_when_moya_is_high_even_if_zhen_is_low(self):
        now = 1_781_318_900.0
        run_state = world_boss._blank_run_state(now)
        run_state.update(
            {
                "active": True,
                "last_status_at": now,
                "phase": "第二阶段·斩灵压顶",
                "hp_percent": 71,
                "moya": 89,
                "zhen": 56,
                "summary": {"镇魂": 4, "护阵": 4, "强攻": 0, "破幡": 0},
            }
        )
        identity_state = {"world_boss_action_count": 0, "world_boss_action_limit": 5, "world_boss_attack_count": 0}

        self.assertEqual("镇魂", world_boss.choose_world_boss_action(3907536807, identity_state, run_state, now=now))

    def test_phase_two_high_moya_above_rescue_threshold_prefers_suppress_when_closer(self):
        now = 1_781_318_950.0
        run_state = world_boss._blank_run_state(now)
        run_state.update(
            {
                "active": True,
                "last_status_at": now,
                "phase": "第二阶段·斩灵压顶",
                "hp_percent": 70,
                "moya": 89,
                "zhen": 21,
                "summary": {"镇魂": 8, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )
        identity_state = {"world_boss_action_count": 0, "world_boss_action_limit": 5, "world_boss_attack_count": 0}

        self.assertEqual("镇魂", world_boss.choose_world_boss_action(3907536807, identity_state, run_state, now=now))

    def test_phase_two_extreme_moya_collapsed_zhen_does_not_guard(self):
        now = 1_781_318_955.0
        run_state = world_boss._blank_run_state(now)
        run_state.update(
            {
                "active": True,
                "last_status_at": now,
                "phase": "第二阶段·斩灵压顶",
                "hp_percent": 89,
                "moya": 97,
                "zhen": 13,
                "summary": {"镇魂": 8, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )
        identity_state = {"world_boss_action_count": 0, "world_boss_action_limit": 5, "world_boss_attack_count": 0}

        self.assertEqual("镇魂", world_boss.choose_world_boss_action(3907536807, identity_state, run_state, now=now))

    async def test_phase_two_strong_window_interrupts_round_gap_for_ready_strong_attacker(self):
        strong_id = 301299112
        cooling_id = 8659059191
        strong_state = self._register(strong_id, label="jfdffdddd")
        cooling_state = self._register(cooling_id, label="WalterWA2000")
        now = 1_781_318_950.0
        strong_state["world_boss_action_count"] = 1
        strong_state["world_boss_last_action_at"] = now - world_boss.WORLD_BOSS_ACTION_COOLDOWN_SEC - 5
        cooling_state["world_boss_action_count"] = 1
        cooling_state["world_boss_last_action_at"] = now - 20
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": "2026-06-13:fast-phase",
                "opened_at": now - 120,
                "phase": "第一阶段·万火归源",
                "hp_percent": 97,
                "moya": 3,
                "zhen": 93,
                "last_status_at": now - 10,
                "round_started_at": now - 70,
                "round_completed_at": now - 30,
                "next_action_at": now + 60,
                "summary": {"镇魂": 12, "护阵": 0, "强攻": 0, "破幡": 11},
            }
        )

        with (
            patch.object(world_boss.time, "time", return_value=now),
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss.asyncio, "sleep", new=AsyncMock()),
            patch.object(
                world_boss,
                "send_game_command",
                new=AsyncMock(return_value=SimpleNamespace(id=9801, sent_at=now + 1)),
            ) as send_mock,
            patch.object(world_boss, "send_audit_log", new=AsyncMock()),
        ):
            await world_boss.handle_world_boss_broadcast(SAFE_PHASE2_STATUS, now, event=SimpleNamespace(id=9800))
            await self._await_world_boss_round_task()

        send_mock.assert_awaited_once()
        self.assertEqual(".讨伐青元子 强攻", send_mock.await_args.args[0])
        self.assertEqual(strong_id, send_mock.await_args.kwargs["send_as_id"])
        run_state = state_module.get_world_boss_run_state()
        self.assertEqual("strong:第二阶段·斩灵压顶:70", run_state["last_priority_window_key"])
        self.assertEqual("强攻", strong_state["world_boss_pending_action"])

    async def test_scheduler_phase_two_high_moya_collapsed_zhen_sends_limited_guard_round(self):
        first_id = 8659059191
        second_id = 3504367852
        self._register(first_id, label="WalterWA2000")
        self._register(second_id, label="竹节虫1")
        now = 1_781_318_960.0
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": "2026-06-13:danger",
                "opened_at": now - 60,
                "phase": "第二阶段·斩灵压顶",
                "hp_percent": 72,
                "moya": 89,
                "zhen": 21,
                "last_status_at": now,
                "next_action_at": 0,
                "summary": {"镇魂": 8, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )

        with (
            patch.object(world_boss.time, "time", return_value=now),
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss.asyncio, "sleep", new=AsyncMock()),
            patch.object(
                world_boss,
                "send_game_command",
                new=AsyncMock(side_effect=[
                    SimpleNamespace(id=9701, sent_at=now),
                    SimpleNamespace(id=9702, sent_at=now + world_boss.WORLD_BOSS_ACTION_GAP_SEC),
                ]),
            ) as send_mock,
        ):
            await world_boss.run_world_boss_scheduler(now)
            await self._await_world_boss_round_task()

        self.assertEqual(2, send_mock.await_count)
        self.assertEqual([".讨伐青元子 镇魂", ".讨伐青元子 镇魂"], [call.args[0] for call in send_mock.await_args_list])
        self.assertTrue(all(call.kwargs["priority"] == "event_burst" for call in send_mock.await_args_list))
        self.assertEqual(
            [now, now + world_boss.WORLD_BOSS_ACTION_GAP_SEC],
            sorted(
                [
                    state_module.get_identity_state(first_id)["world_boss_pending_since"],
                    state_module.get_identity_state(second_id)["world_boss_pending_since"],
                ]
            ),
        )
        run_state = state_module.get_world_boss_run_state()
        run_state["summary"]["护阵"] = 2
        third_state = self._register(3581351795, label="竹节虫2")
        with (
            patch.object(world_boss.time, "time", return_value=now + 2 * world_boss.WORLD_BOSS_ACTION_GAP_SEC),
            patch.object(world_boss, "save_state", return_value=True),
        ):
            action = world_boss.choose_world_boss_action(
                3581351795,
                third_state,
                run_state,
                now=now + 2 * world_boss.WORLD_BOSS_ACTION_GAP_SEC,
            )
        self.assertEqual("镇魂", action)

    async def test_scheduler_sends_one_round_with_one_second_spacing_and_round_gap(self):
        identity_id = 8659059191
        second_id = 3504367852
        identity_state = self._register(identity_id, label="WalterWA2000")
        second_state = self._register(second_id, label="竹节虫1")
        now = 1_781_318_600.0
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": f"{world_boss.get_day_key(now)}:test",
                "opened_at": now - 60,
                "phase": "第一阶段·万火归源",
                "hp_percent": 100,
                "moya": 20,
                "zhen": 90,
                "last_status_at": now,
                "next_action_at": 0,
                "summary": {"镇魂": 0, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )

        with (
            patch.object(world_boss.time, "time", return_value=now),
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss.asyncio, "sleep", new=AsyncMock()),
            patch.object(
                world_boss,
                "send_game_command",
                new=AsyncMock(side_effect=[
                    SimpleNamespace(id=9001, sent_at=now),
                    SimpleNamespace(id=9002, sent_at=now + world_boss.WORLD_BOSS_ACTION_GAP_SEC),
                ]),
            ) as send_mock,
        ):
            await world_boss.run_world_boss_scheduler(now)
            await self._await_world_boss_round_task()
            self.assertEqual(2, send_mock.await_count)
            first_send_args = send_mock.await_args_list[0]
            second_send_args = send_mock.await_args_list[1]

        self.assertEqual(".讨伐青元子 破幡", first_send_args.args[0])
        self.assertEqual(".讨伐青元子 镇魂", second_send_args.args[0])
        self.assertEqual(0, first_send_args.kwargs["max_retry"])
        self.assertEqual("event_burst", first_send_args.kwargs["priority"])
        self.assertEqual("真仙试锋", first_send_args.kwargs["source_module"])
        self.assertTrue(first_send_args.kwargs["op_id"].endswith(":1:try0"))
        self.assertTrue(second_send_args.kwargs["op_id"].endswith(":1:try0"))
        self.assertEqual("world_boss:2026-06-13:test", first_send_args.kwargs["chain_id"])
        self.assertEqual(1, identity_state["world_boss_action_count"])
        self.assertEqual(1, second_state["world_boss_action_count"])
        self.assertEqual("镇魂", identity_state["world_boss_pending_action"])
        identity_call_index = next(
            index for index, call in enumerate(send_mock.await_args_list) if call.kwargs["send_as_id"] == identity_id
        )
        self.assertEqual(9001 if identity_call_index == 0 else 9002, identity_state["world_boss_pending_msg_id"])
        self.assertEqual(
            world_boss.WORLD_BOSS_ACTION_GAP_SEC,
            abs(second_state["world_boss_pending_since"] - identity_state["world_boss_pending_since"]),
        )
        run_state = state_module.get_world_boss_run_state()
        self.assertEqual(now, run_state["round_started_at"])
        self.assertEqual(now + 2 * world_boss.WORLD_BOSS_ACTION_GAP_SEC, run_state["round_completed_at"])
        self.assertEqual(now + world_boss.WORLD_BOSS_PENDING_TIMEOUT_SEC, run_state["next_action_at"])

    async def test_scheduler_opening_two_rounds_assigns_break_flag_once_per_identity(self):
        identity_ids = [3_100_000_000 + index for index in range(22)]
        for identity_id in identity_ids:
            self._register(identity_id, label=f"真仙{identity_id}")
        now = 1_781_318_650.0
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": "2026-06-13:test22",
                "opened_at": now - 60,
                "phase": "第一阶段·万火归源",
                "hp_percent": 100,
                "moya": 20,
                "zhen": 90,
                "last_status_at": now,
                "next_action_at": 0,
                "summary": {"镇魂": 0, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )
        run1_sends = [
            SimpleNamespace(id=9500 + index, sent_at=now + index * world_boss.WORLD_BOSS_ACTION_GAP_SEC)
            for index in range(len(identity_ids))
        ]
        next_round_at = now + 22 * world_boss.WORLD_BOSS_ACTION_GAP_SEC + world_boss.WORLD_BOSS_ROUND_GAP_SEC
        run2_sends = [
            SimpleNamespace(id=9600 + index, sent_at=next_round_at + index * world_boss.WORLD_BOSS_ACTION_GAP_SEC)
            for index in range(len(identity_ids))
        ]

        with (
            patch.object(world_boss.time, "time", return_value=now),
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss.asyncio, "sleep", new=AsyncMock()),
            patch.object(world_boss, "send_game_command", new=AsyncMock(side_effect=run1_sends + run2_sends)) as send_mock,
        ):
            await world_boss.run_world_boss_scheduler(now)
            await self._await_world_boss_round_task()
            for identity_id in identity_ids:
                world_boss._clear_world_boss_pending_action(state_module.get_identity_state(identity_id))
            state_module.get_world_boss_run_state()["last_status_at"] = next_round_at
            await world_boss.run_world_boss_scheduler(next_round_at)
            await self._await_world_boss_round_task()

        self.assertEqual(44, send_mock.await_count)
        first_round = send_mock.await_args_list[:22]
        second_round = send_mock.await_args_list[22:]
        self.assertEqual(identity_ids, [call.kwargs["send_as_id"] for call in first_round])
        self.assertEqual(identity_ids, [call.kwargs["send_as_id"] for call in second_round])
        self.assertEqual([".讨伐青元子 破幡"] * 11 + [".讨伐青元子 镇魂"] * 11, [call.args[0] for call in first_round])
        self.assertEqual([".讨伐青元子 镇魂"] * 22, [call.args[0] for call in second_round])
        sent_identity_ids = [call.kwargs["send_as_id"] for call in first_round]
        self.assertEqual(22, len(set(sent_identity_ids)))
        self.assertTrue(all(state_module.get_identity_state(identity_id)["world_boss_action_count"] == 2 for identity_id in identity_ids))
        pending_since_values = [
            state_module.get_identity_state(identity_id)["world_boss_pending_since"]
            for identity_id in identity_ids
        ]
        self.assertEqual(
            [next_round_at + index * world_boss.WORLD_BOSS_ACTION_GAP_SEC for index in range(22)],
            pending_since_values,
        )
        run_state = state_module.get_world_boss_run_state()
        self.assertEqual(next_round_at, run_state["round_started_at"])
        self.assertEqual(next_round_at + 22 * world_boss.WORLD_BOSS_ACTION_GAP_SEC, run_state["round_completed_at"])
        self.assertEqual(run_state["round_completed_at"] + world_boss.WORLD_BOSS_ROUND_GAP_SEC, world_boss._next_new_round_at(run_state))

    def test_opening_break_flag_is_first_round_only_when_count_is_not_twenty_two(self):
        identity_ids = [3_200_000_000 + index for index in range(15)]
        for identity_id in identity_ids:
            self._register(identity_id, label=f"真仙{identity_id}")

        first_round = []
        second_round = []
        for identity_id in identity_ids:
            identity_state = state_module.get_identity_state(identity_id)
            identity_state["world_boss_action_count"] = 0
            first_round.append(world_boss._opening_action_for_identity(identity_id, identity_state))
            identity_state["world_boss_action_count"] = 1
            second_round.append(world_boss._opening_action_for_identity(identity_id, identity_state))

        self.assertEqual(["破幡"] * 8 + [""] * 7, first_round)
        self.assertEqual([""] * 15, second_round)

        expanded_ids = identity_ids + [3_200_000_000 + index for index in range(15, 25)]
        for identity_id in expanded_ids[15:]:
            self._register(identity_id, label=f"真仙{identity_id}")
        first_round = []
        second_round = []
        for identity_id in expanded_ids:
            identity_state = state_module.get_identity_state(identity_id)
            identity_state["world_boss_action_count"] = 0
            first_round.append(world_boss._opening_action_for_identity(identity_id, identity_state))
            identity_state["world_boss_action_count"] = 1
            second_round.append(world_boss._opening_action_for_identity(identity_id, identity_state))

        self.assertEqual(["破幡"] * 11 + [""] * 14, first_round)
        self.assertEqual([""] * 25, second_round)

    async def test_open_broadcast_message_id_starts_new_event_even_with_same_text(self):
        identity_id = 8659059191
        identity_state = self._register(identity_id, label="WalterWA2000")
        now = 1_781_318_650.0
        stale_key = world_boss.parse_world_boss_text(OPEN_TEXT, now=now)["event_key"]
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": stale_key,
                "opened_at": now - 120,
                "phase": "第二阶段·斩灵压顶",
                "last_status_at": now - 60,
                "summary": {"镇魂": 43, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )
        identity_state["world_boss_action_count"] = 5
        identity_state["world_boss_exhausted"] = True

        with (
            patch.object(world_boss.time, "time", return_value=now),
            patch.object(world_boss, "save_state", return_value=True),
        ):
            await world_boss.handle_world_boss_broadcast(OPEN_TEXT, now, event=SimpleNamespace(id=990001))

        run_state = state_module.get_world_boss_run_state()
        self.assertEqual(f"2026-06-13:990001", run_state["event_key"])
        self.assertTrue(run_state["active"])
        self.assertEqual({"镇魂": 0, "护阵": 0, "强攻": 0, "破幡": 0}, run_state["summary"])
        self.assertEqual(0, identity_state["world_boss_action_count"])
        self.assertFalse(identity_state["world_boss_exhausted"])

    async def test_status_broadcast_immediately_starts_action_round(self):
        first_id = 8659059191
        second_id = 3504367852
        self._register(first_id, label="WalterWA2000")
        self._register(second_id, label="竹节虫1")
        now = 1_781_318_650.0

        with (
            patch.object(world_boss.time, "time", return_value=now),
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss.asyncio, "sleep", new=AsyncMock()),
            patch.object(
                world_boss,
                "send_game_command",
                new=AsyncMock(side_effect=[
                    SimpleNamespace(id=9601, sent_at=now + 1),
                    SimpleNamespace(id=9602, sent_at=now + 1 + world_boss.WORLD_BOSS_ACTION_GAP_SEC),
                ]),
            ) as send_mock,
        ):
            await world_boss.handle_world_boss_broadcast(OPEN_TEXT, now, event=SimpleNamespace(id=9600))
            await world_boss.handle_world_boss_broadcast(STATUS_TEXT, now + 1, event=SimpleNamespace(id=9603))
            await self._await_world_boss_round_task()

        self.assertEqual(2, send_mock.await_count)
        self.assertEqual([".讨伐青元子 破幡", ".讨伐青元子 镇魂"], [call.args[0] for call in send_mock.await_args_list])

    async def test_status_without_open_resets_stale_closed_event_before_actions(self):
        first_id = 8659059191
        second_id = 3504367852
        first_state = self._register(first_id, label="WalterWA2000")
        second_state = self._register(second_id, label="竹节虫1")
        now = 1_781_318_650.0
        state_module.set_world_boss_run_state(
            {
                "active": False,
                "event_key": "2026-06-12:old",
                "opened_at": now - 2 * 3600,
                "closed_at": now - 1800,
                "phase": "第二阶段·斩灵压顶",
                "last_status_at": now - 1800,
                "round_started_at": now - 1900,
                "round_completed_at": now - 1850,
                "summary": {"镇魂": 43, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )
        first_state["world_boss_action_count"] = 5
        first_state["world_boss_exhausted"] = True
        second_state["world_boss_action_count"] = 5
        second_state["world_boss_exhausted"] = True

        with (
            patch.object(world_boss.time, "time", return_value=now),
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss.asyncio, "sleep", new=AsyncMock()),
            patch.object(
                world_boss,
                "send_game_command",
                new=AsyncMock(side_effect=[
                    SimpleNamespace(id=9701, sent_at=now),
                    SimpleNamespace(id=9702, sent_at=now + world_boss.WORLD_BOSS_ACTION_GAP_SEC),
                ]),
            ) as send_mock,
        ):
            await world_boss.handle_world_boss_broadcast(STATUS_TEXT, now, event=SimpleNamespace(id=9700))
            await self._await_world_boss_round_task()

        run_state = state_module.get_world_boss_run_state()
        self.assertEqual("2026-06-13:status:9700", run_state["event_key"])
        self.assertEqual({"镇魂": 0, "护阵": 0, "强攻": 0, "破幡": 0}, run_state["summary"])
        self.assertEqual(2, send_mock.await_count)
        self.assertEqual(1, first_state["world_boss_action_count"])
        self.assertEqual(1, second_state["world_boss_action_count"])

    async def test_scheduler_starts_next_round_after_round_gap(self):
        first_state = self._register(301299112, label="jfdffdddd")
        second_state = self._register(3504367852, label="竹节虫1")
        now = world_boss.time.time()
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": f"{world_boss.get_day_key(now)}:test",
                "opened_at": now - 60,
                "phase": "第一阶段·万火归源",
                "hp_percent": 100,
                "moya": 86,
                "zhen": 90,
                "last_status_at": now,
                "next_action_at": 0,
                "summary": {"镇魂": 0, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )

        next_round_at = now + 2 * world_boss.WORLD_BOSS_ACTION_GAP_SEC + world_boss.WORLD_BOSS_ROUND_GAP_SEC
        sends = [
            SimpleNamespace(id=9101, sent_at=now),
            SimpleNamespace(id=9102, sent_at=now + world_boss.WORLD_BOSS_ACTION_GAP_SEC),
            SimpleNamespace(id=9103, sent_at=next_round_at),
            SimpleNamespace(id=9104, sent_at=next_round_at + world_boss.WORLD_BOSS_ACTION_GAP_SEC),
        ]
        with (
            patch.object(world_boss.time, "time", return_value=now),
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss.asyncio, "sleep", new=AsyncMock()),
            patch.object(world_boss, "send_game_command", new=AsyncMock(side_effect=sends)) as send_mock,
        ):
            await world_boss.run_world_boss_scheduler(now)
            await self._await_world_boss_round_task()
            world_boss._clear_world_boss_pending_action(first_state)
            world_boss._clear_world_boss_pending_action(second_state)
            await world_boss.run_world_boss_scheduler(next_round_at)
            await self._await_world_boss_round_task()

        self.assertEqual(4, send_mock.await_count)
        self.assertEqual(2, first_state["world_boss_action_count"])
        self.assertEqual(2, second_state["world_boss_action_count"])
        self.assertEqual("event_burst", send_mock.await_args_list[0].kwargs["priority"])
        self.assertEqual("event_burst", send_mock.await_args_list[1].kwargs["priority"])
        self.assertTrue(send_mock.await_args_list[2].kwargs["op_id"].endswith(":2:try0"))
        self.assertTrue(send_mock.await_args_list[3].kwargs["op_id"].endswith(":2:try0"))

    async def test_pending_action_retries_every_five_seconds_without_consuming_more_actions(self):
        identity_id = 301299112
        identity_state = self._register(identity_id, label="jfdffdddd")
        now = 1_781_318_700.0
        identity_state["world_boss_action_count"] = 1
        identity_state["world_boss_pending_msg_id"] = 9201
        identity_state["world_boss_pending_action"] = "镇魂"
        identity_state["world_boss_pending_since"] = now - world_boss.WORLD_BOSS_PENDING_TIMEOUT_SEC - 1
        identity_state["world_boss_pending_retry_count"] = 0
        identity_state["world_boss_pending_action_seq"] = 1
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": "2026-06-13:test",
                "opened_at": now - 60,
                "phase": "第一阶段·万火归源",
                "hp_percent": 100,
                "moya": 86,
                "zhen": 90,
                "last_status_at": now,
                "next_action_at": 0,
                "summary": {"镇魂": 0, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )

        retry2_now = now + world_boss.WORLD_BOSS_PENDING_TIMEOUT_SEC + 1
        retry3_now = now + 2 * world_boss.WORLD_BOSS_PENDING_TIMEOUT_SEC + 2
        with (
            patch.object(world_boss.time, "time", return_value=now),
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss.asyncio, "sleep", new=AsyncMock()),
            patch.object(
                world_boss,
                "send_game_command",
                new=AsyncMock(side_effect=[
                    SimpleNamespace(id=9202, sent_at=now),
                    SimpleNamespace(id=9203, sent_at=retry2_now),
                    SimpleNamespace(id=9204, sent_at=retry3_now),
                ]),
            ) as send_mock,
        ):
            await world_boss.run_world_boss_scheduler(now)
            await self._await_world_boss_round_task()
            state_module.get_world_boss_run_state()["last_status_at"] = retry2_now
            await world_boss.run_world_boss_scheduler(retry2_now)
            await self._await_world_boss_round_task()
            state_module.get_world_boss_run_state()["last_status_at"] = retry3_now
            await world_boss.run_world_boss_scheduler(retry3_now)
            await self._await_world_boss_round_task()

        self.assertEqual(3, send_mock.await_count)
        self.assertTrue(send_mock.await_args_list[0].kwargs["op_id"].endswith(":1:try1"))
        self.assertTrue(send_mock.await_args_list[1].kwargs["op_id"].endswith(":1:try2"))
        self.assertTrue(send_mock.await_args_list[2].kwargs["op_id"].endswith(":1:try3"))
        self.assertEqual(1, identity_state["world_boss_action_count"])
        self.assertEqual(9204, identity_state["world_boss_pending_msg_id"])
        self.assertEqual("镇魂", identity_state["world_boss_pending_action"])
        self.assertNotIn("放弃", identity_state["world_boss_last_error"])

    async def test_stale_status_with_pending_action_waits_for_retry_without_status_query(self):
        identity_id = 301299112
        identity_state = self._register(identity_id, label="jfdffdddd")
        now = 1_781_318_900.0
        pending_since = now - 30
        identity_state["world_boss_action_count"] = 1
        identity_state["world_boss_pending_msg_id"] = 9601
        identity_state["world_boss_pending_action"] = "镇魂"
        identity_state["world_boss_pending_since"] = pending_since
        identity_state["world_boss_pending_retry_count"] = 0
        identity_state["world_boss_pending_action_seq"] = 1
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": "2026-06-13:stale-pending",
                "opened_at": now - 600,
                "phase": "第一阶段·万火归源",
                "hp_percent": 100,
                "moya": 86,
                "zhen": 90,
                "last_status_at": now - world_boss.WORLD_BOSS_STATUS_STALE_SEC - 1,
                "next_status_query_at": now - 1,
                "next_action_at": 0,
                "summary": {"镇魂": 0, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "send_game_command", new=AsyncMock()) as send_mock,
            patch.object(world_boss, "send_audit_log", new=AsyncMock()),
        ):
            await world_boss.run_world_boss_scheduler(now)
            send_mock.assert_not_awaited()
            handled = await world_boss.handle_world_boss_reply(
                SUPPRESS_REPLY,
                now + 5,
                matched_family="world_boss",
                reply_context={"send_as_id": identity_id, "family": "world_boss"},
                current_msg_id=9602,
            )

        self.assertTrue(handled)
        self.assertEqual(1, identity_state["world_boss_action_count"])
        self.assertEqual(0, identity_state["world_boss_pending_msg_id"])
        self.assertEqual("", identity_state["world_boss_pending_action"])

    async def test_scheduler_does_not_probe_status_without_active_event(self):
        identity_id = 301299112
        self._register(identity_id, label="jfdffdddd")
        now = world_boss.datetime(2026, 6, 13, 13, 30, tzinfo=world_boss.TZ_LOCAL).timestamp()

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9101, sent_at=now + 1))) as send_mock,
        ):
            await world_boss.run_world_boss_scheduler(now)
            await world_boss.run_world_boss_scheduler(now + 5)

        send_mock.assert_not_awaited()

    async def test_scheduler_clears_inactive_status_residue_without_resending(self):
        identity_id = 301299112
        identity_state = self._register(identity_id, label="jfdffdddd")
        now = world_boss.time.time()
        identity_state["world_boss_last_error"] = "战况查询无回复战况查询发送失败"
        state_module.set_world_boss_run_state(
            {
                "active": False,
                "event_key": "",
                "last_result": "败退",
                "next_status_query_at": now - 1,
                "next_action_at": now - 1,
            }
        )

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "send_game_command", new=AsyncMock()) as send_mock,
        ):
            await world_boss.run_world_boss_scheduler(now)

        send_mock.assert_not_awaited()
        run_state = state_module.get_world_boss_run_state()
        self.assertEqual(0, run_state["next_status_query_at"])
        self.assertEqual(0, run_state["next_action_at"])
        self.assertEqual("", identity_state["world_boss_last_error"])

    async def test_active_status_query_no_reply_retries_after_reply_timeout_with_retry_marker(self):
        identity_id = 301299112
        identity_state = self._register(identity_id, label="jfdffdddd")
        now = world_boss.time.time()
        retry_now = now + world_boss.WORLD_BOSS_STATUS_PENDING_TIMEOUT_SEC + 2
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": f"{world_boss.get_day_key(now)}:status-retry",
                "opened_at": now - 60,
                "remaining_sec": 1200,
                "last_status_at": now - world_boss.WORLD_BOSS_STATUS_STALE_SEC - 1,
                "next_status_query_at": now - 1,
                "next_action_at": now - 1,
            }
        )

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "clear_pending_tasks_by_commands") as clear_mock,
            patch.object(
                world_boss,
                "send_game_command",
                new=AsyncMock(side_effect=[
                    SimpleNamespace(id=9101, sent_at=now + 1),
                    SimpleNamespace(id=9102, sent_at=retry_now + 1),
                ]),
            ) as send_mock,
        ):
            await world_boss.run_world_boss_scheduler(now)
            await world_boss.run_world_boss_scheduler(retry_now)

        self.assertEqual(2, send_mock.await_count)
        first_call, retry_call = send_mock.await_args_list
        self.assertEqual(".世界boss", first_call.args[0])
        self.assertEqual("event_burst", first_call.kwargs["priority"])
        self.assertTrue(first_call.kwargs["op_id"].endswith(f":status:{identity_id}:try0:{int(now)}"))
        self.assertEqual(".世界boss", retry_call.args[0])
        self.assertEqual("retry", retry_call.kwargs["priority"])
        self.assertTrue(retry_call.kwargs["op_id"].endswith(f":status:{identity_id}:try1:{int(retry_now)}"))
        clear_mock.assert_called_with({".世界boss"}, send_as_id=identity_id)
        self.assertEqual(9102, identity_state["world_boss_pending_msg_id"])
        self.assertEqual("status", identity_state["world_boss_pending_action"])
        self.assertEqual(1, identity_state["world_boss_pending_retry_count"])

    async def test_scheduler_clears_stale_status_pending_without_resending(self):
        identity_id = 301299112
        identity_state = self._register(identity_id, label="jfdffdddd")
        now = world_boss.datetime(2026, 6, 25, 7, 20, tzinfo=world_boss.TZ_LOCAL).timestamp()
        identity_state["world_boss_pending_msg_id"] = 10887401
        identity_state["world_boss_pending_action"] = "status"
        identity_state["world_boss_pending_since"] = now - 10
        identity_state["world_boss_pending_retry_count"] = 14
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": "2026-06-23:10796803",
                "opened_at": now - world_boss.WORLD_BOSS_EVENT_TTL_SEC - 3600,
                "last_status_at": now - 3600,
                "next_action_at": now - 10,
                "next_status_query_at": now - 10,
                "summary": {"镇魂": 1, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "clear_pending_tasks_by_commands") as clear_mock,
            patch.object(world_boss, "send_game_command", new=AsyncMock()) as send_mock,
        ):
            await world_boss.run_world_boss_scheduler(now)

        send_mock.assert_not_awaited()
        clear_mock.assert_called_with(world_boss.WORLD_BOSS_PENDING_COMMANDS)
        run_state = state_module.get_world_boss_run_state()
        self.assertFalse(run_state["active"])
        self.assertEqual("超时结束", run_state["last_result"])
        self.assertEqual(0, identity_state["world_boss_pending_msg_id"])
        self.assertEqual("", identity_state["world_boss_pending_action"])
        self.assertEqual(0, identity_state["world_boss_pending_since"])
        self.assertEqual(0, identity_state["world_boss_pending_retry_count"])

    async def test_scheduler_archives_inactive_old_event_pending_without_resending(self):
        identity_id = 301299112
        identity_state = self._register(identity_id, label="jfdffdddd")
        now = world_boss.datetime(2026, 6, 25, 7, 20, tzinfo=world_boss.TZ_LOCAL).timestamp()
        identity_state["world_boss_pending_msg_id"] = 10887188
        identity_state["world_boss_pending_action"] = "status"
        identity_state["world_boss_pending_since"] = now - 2 * 86400
        identity_state["world_boss_pending_retry_count"] = 14
        state_module.set_world_boss_run_state(
            {
                "active": False,
                "event_key": "2026-06-23:10796803",
                "opened_at": now - 2 * 86400,
                "closed_at": now - 2 * 86400 + 1800,
                "last_result": "等待结算",
                "last_status_at": now - 2 * 86400,
                "next_action_at": now - 1,
                "next_status_query_at": now - 1,
                "summary": {"镇魂": 22, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "clear_pending_tasks_by_commands") as clear_mock,
            patch.object(world_boss, "send_game_command", new=AsyncMock()) as send_mock,
        ):
            await world_boss.run_world_boss_scheduler(now)

        send_mock.assert_not_awaited()
        clear_mock.assert_called_with(world_boss.WORLD_BOSS_PENDING_COMMANDS)
        run_state = state_module.get_world_boss_run_state()
        self.assertFalse(run_state["active"])
        self.assertEqual("", run_state["event_key"])
        self.assertEqual(0, run_state["opened_at"])
        self.assertEqual(0, run_state["next_action_at"])
        self.assertEqual(0, run_state["next_status_query_at"])
        self.assertEqual(0, identity_state["world_boss_pending_msg_id"])
        self.assertEqual("", identity_state["world_boss_pending_action"])
        self.assertEqual("事件已过期", identity_state["world_boss_last_error"])

    async def test_status_retry_blocked_when_event_key_is_from_previous_day(self):
        identity_id = 301299112
        identity_state = self._register(identity_id, label="jfdffdddd")
        now = world_boss.datetime(2026, 6, 25, 7, 20, tzinfo=world_boss.TZ_LOCAL).timestamp()
        identity_state["world_boss_pending_msg_id"] = 10887188
        identity_state["world_boss_pending_action"] = "status"
        identity_state["world_boss_pending_since"] = now - 10
        identity_state["world_boss_pending_retry_count"] = 1
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": "2026-06-23:10796803",
                "opened_at": 0,
                "last_status_at": now - 3600,
                "next_action_at": now - 1,
                "next_status_query_at": now - 1,
            }
        )

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "clear_pending_tasks_by_commands") as clear_mock,
            patch.object(world_boss, "send_game_command", new=AsyncMock()) as send_mock,
        ):
            await world_boss.run_world_boss_scheduler(now)

        send_mock.assert_not_awaited()
        clear_mock.assert_called_with(world_boss.WORLD_BOSS_PENDING_COMMANDS)
        self.assertFalse(state_module.get_world_boss_run_state()["active"])
        self.assertEqual(0, identity_state["world_boss_pending_msg_id"])
        self.assertEqual("事件已过期", identity_state["world_boss_last_error"])

    async def test_conclusion_log_is_deduped_by_event_text(self):
        now = 1_781_319_000.0
        self._register(8659059191, label="WalterWA2000", world_boss_enabled=True)
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": "2026-06-13:test",
                "opened_at": now - 1800,
                "summary": {"镇魂": 0, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "send_audit_log", new=AsyncMock()) as audit_mock,
        ):
            await world_boss.handle_world_boss_broadcast(CONCLUSION_TEXT, now, event=SimpleNamespace(id=10001))
            await world_boss.handle_world_boss_broadcast(CONCLUSION_TEXT, now + 1, event=SimpleNamespace(id=10002))

        self.assertEqual(1, audit_mock.await_count)
        self.assertFalse(state_module.get_world_boss_run_state()["active"])

    async def test_broadcast_is_quiet_when_no_identity_enabled(self):
        now = 1_781_319_100.0
        self._register(8659059191, label="WalterWA2000", world_boss_enabled=False)

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "send_audit_log", new=AsyncMock()) as audit_mock,
        ):
            opened = await world_boss.handle_world_boss_broadcast(OPEN_TEXT, now, event=SimpleNamespace(id=11001))
            status = await world_boss.handle_world_boss_broadcast(STATUS_TEXT, now + 1, event=SimpleNamespace(id=11002))

        self.assertFalse(opened)
        self.assertFalse(status)
        audit_mock.assert_not_awaited()
        self.assertFalse(state_module.get_world_boss_run_state().get("active"))

    async def test_inactive_broadcast_closes_stale_event_and_clears_pending_without_enabled_identities(self):
        identity_id = 8659059191
        identity_state = self._register(identity_id, label="WalterWA2000", world_boss_enabled=False)
        identity_state["world_boss_pending_msg_id"] = 9301
        identity_state["world_boss_pending_action"] = "status"
        identity_state["world_boss_pending_since"] = 1_781_319_000.0
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": "2026-06-13:stale",
                "opened_at": 1_781_319_000.0,
                "phase": "第一阶段·万火归源",
                "last_status_at": 1_781_319_000.0,
            }
        )

        with patch.object(world_boss, "save_state", return_value=True):
            handled = await world_boss.handle_world_boss_broadcast(
                "当前没有进行中的【真仙试锋】。",
                1_781_319_120.0,
                event=SimpleNamespace(id=11003),
            )

        self.assertTrue(handled)
        self.assertFalse(state_module.get_world_boss_run_state()["active"])
        self.assertEqual(0, identity_state["world_boss_pending_msg_id"])
        self.assertEqual("", identity_state["world_boss_pending_action"])
        self.assertEqual(0, identity_state["world_boss_pending_since"])

    async def test_conclusion_broadcast_closes_stale_event_silently_without_enabled_identities(self):
        identity_id = 8659059191
        identity_state = self._register(identity_id, label="WalterWA2000", world_boss_enabled=False)
        identity_state["world_boss_pending_msg_id"] = 9401
        identity_state["world_boss_pending_action"] = "镇魂"
        identity_state["world_boss_pending_since"] = 1_781_319_000.0
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": "2026-06-13:stale",
                "opened_at": 1_781_319_000.0,
                "summary": {"镇魂": 1, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "send_audit_log", new=AsyncMock()) as audit_mock,
        ):
            handled = await world_boss.handle_world_boss_broadcast(
                CONCLUSION_TEXT,
                1_781_319_120.0,
                event=SimpleNamespace(id=11004),
            )

        run_state = state_module.get_world_boss_run_state()
        self.assertTrue(handled)
        self.assertFalse(run_state["active"])
        self.assertEqual("败退", run_state["last_result"])
        self.assertEqual(0, identity_state["world_boss_pending_msg_id"])
        self.assertEqual("", identity_state["world_boss_pending_action"])
        self.assertEqual(0, identity_state["world_boss_pending_since"])
        audit_mock.assert_not_awaited()

    async def test_passive_action_reply_updates_identity_count_and_last_action_time(self):
        identity_id = 3907536807
        identity_state = self._register(identity_id, label="三少爷的剑")
        now = 1_781_319_200.0
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": "2026-06-13:test",
                "opened_at": now - 60,
                "phase": "第一阶段·万火归源",
                "last_status_at": now,
                "summary": {"镇魂": 0, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "send_audit_log", new=AsyncMock()),
        ):
            handled = await world_boss.handle_world_boss_reply(
                SUPPRESS_REPLY,
                now,
                matched_family="world_boss",
                reply_context={"send_as_id": identity_id, "family": "world_boss"},
                current_msg_id=12001,
            )

        self.assertTrue(handled)
        self.assertEqual(1, identity_state["world_boss_action_count"])
        self.assertEqual("镇魂", identity_state["world_boss_last_action"])
        self.assertEqual(now, identity_state["world_boss_last_action_at"])
        self.assertEqual(1, state_module.get_world_boss_run_state()["summary"]["镇魂"])

    async def test_pending_action_reply_does_not_double_count_send_owned_action(self):
        identity_id = 301299112
        identity_state = self._register(identity_id, label="jfdffdddd")
        identity_state["world_boss_action_count"] = 1
        identity_state["world_boss_pending_msg_id"] = 9301
        identity_state["world_boss_pending_action"] = "镇魂"
        identity_state["world_boss_pending_since"] = 1_781_319_200.0
        now = 1_781_319_205.0
        state_module.set_world_boss_run_state(
            {
                "active": True,
                "event_key": "2026-06-13:test",
                "opened_at": now - 60,
                "phase": "第一阶段·万火归源",
                "last_status_at": now,
                "summary": {"镇魂": 0, "护阵": 0, "强攻": 0, "破幡": 0},
            }
        )

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "send_audit_log", new=AsyncMock()),
        ):
            handled = await world_boss.handle_world_boss_reply(
                SUPPRESS_REPLY,
                now,
                matched_family="world_boss",
                reply_context={"send_as_id": identity_id, "family": "world_boss"},
                current_msg_id=12002,
            )

        self.assertTrue(handled)
        self.assertEqual(1, identity_state["world_boss_action_count"])
        self.assertEqual(0, identity_state["world_boss_pending_msg_id"])
        self.assertEqual("", identity_state["world_boss_pending_action"])
        self.assertEqual(1, state_module.get_world_boss_run_state()["summary"]["镇魂"])

    def test_manifest_maps_world_boss_to_module(self):
        self.assertEqual("真仙试锋", module_manifest.get_module_name_for_reply_family("world_boss"))
        self.assertEqual("真仙试锋", module_manifest.get_module_name_for_replay_module("world_boss"))


if __name__ == "__main__":
    unittest.main()
