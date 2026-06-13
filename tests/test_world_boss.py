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
    "- .世界boss 查看战况\n"
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
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _register(self, identity_id, *, label="", world_boss_enabled=True):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username=label or str(identity_id), label=label or str(identity_id), enabled=True)
        identity_state = state_module.get_identity_state(identity_id)
        identity_state["world_boss_enabled"] = bool(world_boss_enabled)
        return identity_state

    def test_parse_real_world_boss_text_shapes(self):
        opened = world_boss.parse_world_boss_text(OPEN_TEXT, now=1_781_318_000.0)
        status = world_boss.parse_world_boss_text(STATUS_TEXT, now=1_781_318_000.0)
        suppress = world_boss.parse_world_boss_text(SUPPRESS_REPLY, now=1_781_318_000.0)
        guard = world_boss.parse_world_boss_text(GUARD_REPLY, now=1_781_318_000.0)
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
        self.assertEqual("强攻", attack["action"])
        self.assertEqual("3.30亿亿", attack["damage"])
        self.assertEqual("exhausted", exhausted["type"])
        self.assertEqual(5, exhausted["own_actions"])
        self.assertEqual("inactive", inactive["type"])
        self.assertEqual("conclusion", conclusion["type"])
        self.assertEqual("败退", conclusion["result"])
        self.assertEqual(66, conclusion["participants"])
        self.assertIsNone(world_boss.parse_world_boss_text("荣誉称号: 【真仙试锋】"))

    def test_dangerous_phase_two_blocks_strong_attack_even_for_wa(self):
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

    async def test_scheduler_sends_one_no_retry_action_and_respects_global_gap(self):
        identity_id = 8659059191
        identity_state = self._register(identity_id, label="WalterWA2000")
        now = 1_781_318_600.0
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

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9001, sent_at=now + 1))) as send_mock,
        ):
            await world_boss.run_world_boss_scheduler(now)
            send_mock.assert_awaited_once()
            first_send_args = send_mock.await_args
            send_mock.reset_mock()
            await world_boss.run_world_boss_scheduler(now + 2)
            send_mock.assert_not_awaited()

        self.assertEqual(".讨伐青元子 镇魂", first_send_args.args[0])
        self.assertEqual(0, first_send_args.kwargs["max_retry"])
        self.assertEqual("真仙试锋", first_send_args.kwargs["source_module"])
        self.assertEqual(1, identity_state["world_boss_action_count"])
        self.assertEqual("镇魂", identity_state["world_boss_pending_action"])
        self.assertEqual(9001, identity_state["world_boss_pending_msg_id"])
        run_state = state_module.get_world_boss_run_state()
        self.assertEqual(now + 1 + world_boss.WORLD_BOSS_ACTION_GAP_SEC, run_state["next_action_at"])

    async def test_scheduler_sends_status_query_once_in_fallback_window(self):
        identity_id = 301299112
        self._register(identity_id, label="jfdffdddd")
        now = world_boss.datetime(2026, 6, 13, 13, 30, tzinfo=world_boss.TZ_LOCAL).timestamp()

        with (
            patch.object(world_boss, "save_state", return_value=True),
            patch.object(world_boss, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9101, sent_at=now + 1))) as send_mock,
        ):
            await world_boss.run_world_boss_scheduler(now)
            await world_boss.run_world_boss_scheduler(now + 5)

        send_mock.assert_awaited_once()
        self.assertEqual(".世界boss 查看战况", send_mock.await_args.args[0])
        self.assertEqual(0, send_mock.await_args.kwargs["max_retry"])
        self.assertEqual("真仙试锋", send_mock.await_args.kwargs["source_module"])

    async def test_conclusion_log_is_deduped_by_event_text(self):
        now = 1_781_319_000.0
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

    def test_manifest_maps_world_boss_to_module(self):
        self.assertEqual("真仙试锋", module_manifest.get_module_name_for_reply_family("world_boss"))
        self.assertEqual("真仙试锋", module_manifest.get_module_name_for_replay_module("world_boss"))


if __name__ == "__main__":
    unittest.main()
