import asyncio
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model import app
from model.features import deep_retreat, passive_inbox, yinluo
from model.real_message_replay import get_real_message_text, iter_real_message_samples


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "real_message_samples.json"


def real_text(sample_id):
    return get_real_message_text(FIXTURE_PATH, sample_id)


class YinluoParserTests(unittest.TestCase):
    def test_banner_panel_parses_core_fields_and_slots(self):
        parsed = yinluo.parse_yinluo_text(
            real_text("yinluo.banner.basic"),
            now=1_779_450_000.0,
            family="yinluo_banner",
        )

        self.assertEqual("阴罗幡", parsed["action"])
        self.assertEqual("panel", parsed["result"])
        self.assertEqual("水镜真人", parsed["banner_owner"])
        self.assertEqual("灭法幡", parsed["banner_name"])
        self.assertEqual("三阶中品", parsed["banner_rank"])
        self.assertEqual(269465, parsed["sha_current"])
        self.assertEqual(25000, parsed["sha_max"])
        self.assertEqual(100, parsed["sha_percent"])
        self.assertEqual(54, parsed["soul_total"])
        self.assertEqual(11, parsed["battle_bonus_percent"])
        self.assertEqual(1, parsed["ready_slots"])
        self.assertEqual([4], parsed["ready_slot_numbers"])
        self.assertEqual([{"slot": 4, "target": "凶兽戾魄"}], parsed["ready_slots_detail"])
        self.assertEqual(1, parsed["refining_slots"])
        self.assertEqual([1, 2, 3], parsed["empty_slot_numbers"])
        self.assertEqual([5], parsed["refining_slot_numbers"])
        self.assertEqual("凶兽戾魄", parsed["refining_slots_detail"][0]["target"])
        self.assertEqual(3 * 3600 + 28 * 60 + 48, parsed["refining_slots_detail"][0]["remaining_sec"])
        self.assertEqual(191, parsed["soul_stocks"]["妖兽精魄"])

    def test_sanshaoye_banner_parses_lineage_traits_and_ready_slot_number(self):
        parsed = yinluo.parse_yinluo_text(
            real_text("yinluo.banner.sanshaoye_ready"),
            now=1_781_281_959.0,
            family="yinluo_banner",
        )

        self.assertEqual("缘初子", parsed["banner_owner"])
        self.assertEqual("阴罗本幡", parsed["banner_name"])
        self.assertEqual("三阶下品", parsed["banner_rank"])
        self.assertEqual(100, parsed["sha_current"])
        self.assertEqual(1, parsed["ready_slots"])
        self.assertEqual([1], parsed["ready_slot_numbers"])
        self.assertEqual([2, 3, 4, 5, 6, 7, 8, 9], parsed["empty_slot_numbers"])
        self.assertEqual(4, parsed["soul_stocks"]["妖兽精魄"])
        self.assertEqual(0, parsed["soul_lineage"]["妖兽精魄"])
        self.assertEqual("+0%", parsed["banner_traits"]["斗法拘魂"])
        self.assertNotIn("妖兽精魄 · 血煞幡", parsed["soul_stocks"])
        self.assertNotIn("凶兽戾魄 · 灭法幡", parsed["soul_stocks"])

    def test_banner_parses_exhausted_slots_and_live_lineage_traits(self):
        parsed = yinluo.parse_yinluo_text(
            "【缘初子的阴罗幡】\n"
            "本命魔兵: 灭法幡\n"
            "幡体等阶: 三阶下品\n"
            "煞气池: 1060 / 15000 (7%)\n"
            "幡威状态: 幡息沉潜\n"
            "主魂流派: 灭法幡\n"
            "幡魂总炼化: 10 缕\n"
            "武器战力加成: +4% (幡阶 3% / 主魂 1% / 煞涌 0%)\n"
            "\n"
            "幡魂谱系:\n"
            " - 怨魂 · 摄魂幡: 0 缕\n"
            " - 修士残魂 · 噬灵幡: 0 缕\n"
            " - 妖兽精魄 · 血煞幡: 1 缕\n"
            " - 凶兽戾魄 · 灭法幡: 9 缕 ★\n"
            "\n"
            "当前特性:\n"
            " - 斗法拘魂: +10%\n"
            " - 召魔镇压: +10%\n"
            "\n"
            "魂魄储备:\n"
            " - 妖兽精魄: 40 缕\n"
            "\n"
            "炼化槽:\n"
            "1号槽: [精华已成] - 凶兽戾魄\n"
            "2号槽: [精华已成] - 妖兽精魄\n"
            "3号槽: [魂力枯竭] ❗\n"
            "4号槽: [空闲]\n",
            now=1_781_704_200.0,
            family="yinluo_banner",
        )

        self.assertEqual([1, 2], parsed["ready_slot_numbers"])
        self.assertEqual([3], parsed["exhausted_slot_numbers"])
        self.assertEqual([4], parsed["empty_slot_numbers"])
        self.assertEqual(9, parsed["soul_lineage"]["凶兽戾魄"])
        self.assertEqual("+10%", parsed["banner_traits"]["召魔镇压"])

    def test_banner_panel_parses_markdown_decorated_slot_lines(self):
        parsed = yinluo.parse_yinluo_text(
            "**【缘初子的阴罗幡】**\n"
            "**本命魔兵：** `灭法幡`\n"
            "**煞气池：** 1060 / 350000 (0%)\n"
            "**炼化槽：**\n"
            "- **1号槽：** `[炼化中]` - 凶兽戾魄 (剩余：2小时)\n"
            "- **2号槽：** `[精华已成]` - 妖兽精魄\n"
            "- **3号槽：** `[空闲]`\n",
            now=1_784_734_703.0,
            family="yinluo_banner",
        )

        self.assertEqual(1060, parsed["sha_current"])
        self.assertEqual(350000, parsed["sha_max"])
        self.assertEqual([1], parsed["refining_slot_numbers"])
        self.assertEqual([2], parsed["ready_slot_numbers"])
        self.assertEqual([3], parsed["empty_slot_numbers"])
        self.assertEqual(2 * 3600, parsed["refining_slots_detail"][0]["remaining_sec"])

    def test_banner_zero_second_refining_slot_schedules_recheck_not_collect(self):
        now = 1_781_443_187.0
        parsed = yinluo.parse_yinluo_text(
            "【缘初子的阴罗幡】\n"
            "煞气池: 2140 / 15000 (14%)\n"
            "魂魄储备:\n"
            " - 妖兽精魄: 15 缕\n\n"
            "炼化槽:\n"
            "3号槽: [炼化中] - 凶兽戾魄 (剩余: 0秒)\n"
            "6号槽: [空闲]",
            now=now,
            family="yinluo_banner",
        )

        self.assertEqual([], parsed["ready_slot_numbers"])
        self.assertEqual([3], parsed["refining_slot_numbers"])
        detail = parsed["refining_slots_detail"][0]
        self.assertEqual(0, detail["remaining_sec"])
        self.assertNotIn("finish_time", detail)
        self.assertEqual(now + yinluo.YINLUO_REFINING_DUE_RECHECK_SEC, detail["recheck_time"])

    def test_empty_collect_result_is_not_treated_as_success(self):
        parsed = yinluo.parse_yinluo_text(
            "收取成功！\n你从 0 个炼化槽中获得了: ！",
            now=1_781_430_176.0,
        )

        self.assertEqual("收取精华", parsed["action"])
        self.assertEqual("empty", parsed["result"])
        self.assertEqual(0, parsed["last_collect_count"])
        self.assertIn("查幡校准", parsed["last_error"])

    def test_demon_summon_cooldown_convert_and_retreat_parse(self):
        now = 1_779_450_000.0
        success = yinluo.parse_yinluo_text(real_text("yinluo.demon_summon.success"), now=1_779_450_000.0)
        cooldown = yinluo.parse_yinluo_text(real_text("yinluo.demon_summon.cooldown"), now=1_779_450_000.0)
        realm_blocked = yinluo.parse_yinluo_text(real_text("yinluo.demon_summon.realm_blocked"), now=now)
        pending = yinluo.parse_yinluo_text(real_text("yinluo.demon_summon.pending_fight"), now=now)
        failed = yinluo.parse_yinluo_text(real_text("yinluo.demon_summon.failed_backlash"), now=now)
        daily_sacrifice = yinluo.parse_yinluo_text(real_text("yinluo.daily_sacrifice.success"), now=1_779_450_000.0)
        daily_cooldown = yinluo.parse_yinluo_text(real_text("yinluo.daily_sacrifice.cooldown"), now=1_779_450_000.0)
        soothe = yinluo.parse_yinluo_text("安抚成功！\n你消耗了 50 点修为，成功安抚了 1 个炼化槽。", now=1_779_450_000.0)
        convert = yinluo.parse_yinluo_text("【转化成功】\n你成功将 10000 点修为炼化，煞气池增加了 2000 点！", now=1_779_450_000.0)
        retreat = yinluo.parse_yinluo_text(real_text("yinluo.retreat.success_bonus"), now=1_779_450_000.0)

        self.assertEqual("召唤魔影", success["action"])
        self.assertEqual("success", success["result"])
        self.assertEqual("凶兽戾魄", success["last_resource"])
        self.assertEqual("cooldown", cooldown["result"])
        self.assertGreater(cooldown["next_demon_summon_time"], 1_779_450_000.0)
        self.assertEqual("召唤魔影", realm_blocked["action"])
        self.assertEqual("realm_blocked", realm_blocked["result"])
        self.assertEqual("境界尚未达到结丹期", realm_blocked["last_error"])
        self.assertEqual("pending", pending["result"])
        self.assertEqual("failed", failed["result"])
        self.assertEqual(1362, failed["last_backlash_loss"])
        self.assertEqual(now + yinluo.YINLUO_DEMON_SUMMON_OBSERVED_CD_SEC + yinluo.YINLUO_TIME_BUFFER_SEC, failed["next_demon_summon_time"])
        self.assertEqual("每日献祭", daily_sacrifice["action"])
        self.assertEqual(575, daily_sacrifice["last_sha_gain"])
        self.assertEqual(75, daily_sacrifice["last_extra_sha_gain"])
        self.assertNotIn("next_convert_time", daily_sacrifice)
        self.assertEqual(yinluo.get_day_key(1_779_450_000.0), daily_sacrifice["last_daily_sacrifice_day"])
        self.assertGreater(daily_sacrifice["next_daily_sacrifice_time"], 1_779_450_000.0)
        self.assertEqual("每日献祭", daily_cooldown["action"])
        self.assertEqual("cooldown", daily_cooldown["result"])
        self.assertGreater(daily_cooldown["next_daily_sacrifice_time"], 1_779_450_000.0)
        self.assertEqual("安抚幡灵", soothe["action"])
        self.assertEqual("success", soothe["result"])
        self.assertEqual(50, soothe["last_soothe_cost"])
        self.assertEqual(1, soothe["last_soothe_count"])
        self.assertEqual("化功为煞", convert["action"])
        self.assertEqual(10000, convert["last_convert_amount"])
        self.assertEqual(2000, convert["last_sha_gain"])
        self.assertGreater(convert["next_convert_time"], now)
        self.assertEqual(68, retreat["last_bonus_gain"])

    def test_recent_convert_and_refine_text_parse(self):
        now = 1_781_341_000.0
        pending = yinluo.parse_yinluo_text("你开始运转魔功，试图将 1000 点修为凝练为纯粹的煞气...", now=now)
        success = yinluo.parse_yinluo_text("【转化成功】\n你成功将 1000 点修为炼化，煞气池增加了 200 点！", now=now)
        cooldown = yinluo.parse_yinluo_text("你刚施展过此术，经脉尚在恢复，请在 59分钟50秒 后再试。", now=now)
        invalid = yinluo.parse_yinluo_text("每次转化的修为需在 1000 至 50000 点之间。", now=now)
        refine_success = yinluo.parse_yinluo_text("一缕【凶兽戾魄】被强行打入7号炼化槽，在煞气的包裹下发出阵阵哀嚎，炼化已开始。", now=now)
        refine_shortage = yinluo.parse_yinluo_text("你的煞气不足！炼化需要消耗 1000 点煞气。", now=now)
        refine_missing = yinluo.parse_yinluo_text("你的魂魄袋中没有【凶兽精魄】。", now=now)

        self.assertEqual("pending", pending["result"])
        self.assertEqual("化功为煞", success["action"])
        self.assertEqual(1000, success["last_convert_amount"])
        self.assertEqual(200, success["last_sha_gain"])
        self.assertEqual(now + yinluo.YINLUO_CONVERT_OBSERVED_CD_SEC + yinluo.YINLUO_TIME_BUFFER_SEC, success["next_convert_time"])
        self.assertEqual(now + 60 * 60 + yinluo.YINLUO_TIME_BUFFER_SEC, success["next_convert_time"])
        self.assertEqual("cooldown", cooldown["result"])
        self.assertEqual(now + 59 * 60 + 50 + yinluo.YINLUO_TIME_BUFFER_SEC, cooldown["next_convert_time"])
        self.assertEqual("invalid_amount", invalid["result"])
        self.assertEqual("囚禁魂魄", refine_success["action"])
        self.assertEqual(7, refine_success["last_refine_slot"])
        self.assertEqual("凶兽戾魄", refine_success["last_resource"])
        self.assertEqual("sha_shortage", refine_shortage["result"])
        self.assertEqual(1000, refine_shortage["last_refine_cost"])
        self.assertEqual("missing_soul", refine_missing["result"])

    def test_blood_forest_pending_success_and_cooldown_parse_real_text(self):
        now = 1_779_450_000.0
        pending = yinluo.parse_yinluo_text(real_text("yinluo.blood_forest.pending"), now=now)
        success = yinluo.parse_yinluo_text(real_text("yinluo.blood_forest.success"), now=now)
        success_extra = yinluo.parse_yinluo_text(real_text("yinluo.blood_forest.success_extra_soul"), now=now)
        cooldown = yinluo.parse_yinluo_text(real_text("yinluo.blood_forest.cooldown"), now=now)

        self.assertEqual("血洗山林", pending["action"])
        self.assertEqual("pending", pending["result"])
        self.assertEqual("血洗山林", success["action"])
        self.assertEqual("success", success["result"])
        self.assertEqual(1, success["last_soul_gain"])
        self.assertIn("一阶妖丹 x1", success["last_resource"])
        self.assertEqual(1, success_extra["last_extra_soul_gain"])
        self.assertIn("妖兽精魄 x1", success_extra["last_resource"])
        self.assertEqual(now + yinluo.YINLUO_BLOOD_FOREST_OBSERVED_CD_SEC + yinluo.YINLUO_TIME_BUFFER_SEC, success["next_blood_forest_time"])
        self.assertEqual("血洗山林", cooldown["action"])
        self.assertEqual("cooldown", cooldown["result"])
        self.assertEqual(now + 3 * 3600 + 59 * 60 + 20 + yinluo.YINLUO_TIME_BUFFER_SEC, cooldown["next_blood_forest_time"])

    def test_non_member_guides_and_resurrection_guard(self):
        not_member = yinluo.parse_yinluo_text(real_text("yinluo.blood_forest.not_member"), now=1_779_450_000.0)
        guide = yinluo.parse_yinluo_text(real_text("yinluo.guide.blood_forest"), now=1_779_450_000.0)
        possess = yinluo.parse_yinluo_text(real_text("yinluo.possess.clue"), now=1_779_450_000.0)

        self.assertEqual("血洗山林", not_member["action"])
        self.assertEqual("not_member", not_member["result"])
        self.assertEqual("血洗山林", guide["action"])
        self.assertEqual("guide", guide["result"])
        self.assertEqual("夺舍", possess["action"])
        self.assertEqual("guide", possess["result"])
        self.assertFalse(yinluo.looks_like_yinluo_text("【天机异闻·夺舍重生】\n先前肉身陨落的 @abc 已成功夺舍重生！"))
        self.assertIsNone(yinluo.parse_yinluo_text("你面前出现了三具可供夺舍的肉身：\n1. 【夺舍 墨竹生】", now=1_779_450_000.0))


class YinluoManualPlanTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        self.identity_id = 3101
        state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(
            self.identity_id,
            username="yinluo_manual",
            label="yinluo_manual",
            sect_name="阴罗宗",
            realm="结丹初期",
            xiuwei_max=50000,
        )

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_banner_query_is_allowed_without_prior_observation(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True

            plan = yinluo.build_yinluo_manual_plan("banner", now=now)

        self.assertTrue(plan["allowed"])
        self.assertEqual(".我的阴罗幡", plan["command"])
        self.assertEqual("yinluo_banner", plan["family"])
        self.assertEqual(0, plan["max_retry"])

    def test_summon_collect_and_convert_use_recent_observation(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "last_action": "阴罗幡",
                "last_result": "panel",
                "next_demon_summon_time": 0,
                "next_blood_forest_time": now + 3600,
                "ready_slots": 1,
                "ready_slot_numbers": [1],
                "sha_current": 600,
                "sha_max": 15000,
                "empty_slots": 8,
                "soul_stocks": {"妖兽精魄": 2},
            }
            summon = yinluo.build_yinluo_manual_plan("demon_summon", now=now)
            daily = yinluo.build_yinluo_manual_plan("daily_sacrifice", now=now)
            collect = yinluo.build_yinluo_manual_plan("collect", now=now)
            convert = yinluo.build_yinluo_manual_plan("convert", "1000", now=now)
            refine = yinluo.build_yinluo_manual_plan("refine", "2 妖兽精魄", now=now)
            state_module.state["yinluo_observation"]["empty_slot_numbers"] = [2]
            refine_wrong_slot = yinluo.build_yinluo_manual_plan("refine", "3 妖兽精魄", now=now)

            state_module.state["yinluo_observation"]["next_demon_summon_time"] = now + 600
            summon_cooldown = yinluo.build_yinluo_manual_plan("demon_summon", now=now)
            state_module.state["yinluo_observation"]["next_daily_sacrifice_time"] = now + 600
            daily_cooldown = yinluo.build_yinluo_manual_plan("献祭", now=now)

            state_module.state["yinluo_observation"]["next_demon_summon_time"] = 0
            state_module.state["yinluo_observation"]["next_daily_sacrifice_time"] = 0
            state_module.state["yinluo_observation"]["last_daily_sacrifice_day"] = yinluo.get_day_key(now)
            daily_done_today = yinluo.build_yinluo_manual_plan("每日献祭", now=now)
            state_module.state["yinluo_observation"]["last_daily_sacrifice_day"] = ""
            state_module.state["yinluo_observation"]["ready_slots"] = 0
            state_module.state["yinluo_observation"]["ready_slot_numbers"] = []
            collect_empty = yinluo.build_yinluo_manual_plan("collect", now=now)

            convert_missing_amount = yinluo.build_yinluo_manual_plan("convert", "", now=now)
            convert_too_small = yinluo.build_yinluo_manual_plan("convert", "400", now=now)
            convert_too_large = yinluo.build_yinluo_manual_plan("convert", "50001", now=now)
            state_module.state["yinluo_observation"]["next_convert_time"] = now + 600
            convert_cooldown = yinluo.build_yinluo_manual_plan("convert", "1000", now=now)
            state_module.state["yinluo_observation"]["next_convert_time"] = 0
            refine_low_sha = yinluo.build_yinluo_manual_plan("refine", "2 凶兽戾魄", now=now)
            refine_missing_target = yinluo.build_yinluo_manual_plan("refine", "2", now=now)

        self.assertTrue(summon["allowed"])
        self.assertEqual(".召唤魔影", summon["command"])
        self.assertTrue(daily["allowed"])
        self.assertEqual(".每日献祭", daily["command"])
        self.assertEqual("yinluo_daily_sacrifice", daily["family"])
        self.assertTrue(collect["allowed"])
        self.assertEqual(".收取精华 1", collect["command"])
        self.assertTrue(convert["allowed"])
        self.assertEqual(".化功为煞 1000", convert["command"])
        self.assertTrue(refine["allowed"])
        self.assertEqual(".囚禁魂魄 2 妖兽精魄", refine["command"])
        self.assertEqual("yinluo_refine", refine["family"])
        self.assertFalse(refine_wrong_slot["allowed"])
        self.assertIn("未记录为空闲槽", refine_wrong_slot["reason"])
        self.assertFalse(summon_cooldown["allowed"])
        self.assertIn("冷却", summon_cooldown["reason"])
        self.assertFalse(daily_cooldown["allowed"])
        self.assertIn("冷却", daily_cooldown["reason"])
        self.assertFalse(daily_done_today["allowed"])
        self.assertIn("今日已记录", daily_done_today["reason"])
        self.assertFalse(collect_empty["allowed"])
        self.assertIn("未记录可收取", collect_empty["reason"])
        self.assertFalse(convert_missing_amount["allowed"])
        self.assertIn("必须指定正整数", convert_missing_amount["reason"])
        self.assertFalse(convert_too_small["allowed"])
        self.assertIn("1000 至 50000", convert_too_small["reason"])
        self.assertFalse(convert_too_large["allowed"])
        self.assertIn("上限", convert_too_large["reason"])
        self.assertFalse(convert_cooldown["allowed"])
        self.assertIn("冷却", convert_cooldown["reason"])
        self.assertFalse(refine_low_sha["allowed"])
        self.assertIn("煞气不足", refine_low_sha["reason"])
        self.assertFalse(refine_missing_target["allowed"])
        self.assertIn("目标", refine_missing_target["reason"])

    def test_convert_blocks_when_known_xiuwei_is_insufficient(self):
        now = 1_780_000_000.0
        state_module.update_send_as_profile(self.identity_id, xiuwei_current=800, xiuwei_max=50000)
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "last_action": "阴罗幡",
                "last_result": "panel",
            }

            convert = yinluo.build_yinluo_manual_plan("convert", "1000", now=now)

        self.assertFalse(convert["allowed"])
        self.assertIn("当前修为 800", convert["reason"])

    def test_active_yinluo_actions_block_during_phaseful_summary_risk(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "running"
            state_module.state["next_deep_retreat_time"] = now + 30
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "last_action": "阴罗幡",
                "last_result": "panel",
                "banner_owner": "缘初子",
                "banner_name": "血煞幡胚",
                "sha_current": 2000,
                "sha_max": 15000,
                "empty_slots": 8,
                "ready_slots": 1,
                "ready_slot_numbers": [1],
                "next_demon_summon_time": 0,
                "next_blood_forest_time": 0,
                "soul_stocks": {"凶兽戾魄": 1},
            }

            banner = yinluo.build_yinluo_manual_plan("banner", now=now)
            refine = yinluo.build_yinluo_manual_plan("refine", "2 凶兽戾魄", now=now)
            collect = yinluo.build_yinluo_manual_plan("collect", now=now)

        self.assertTrue(banner["allowed"])
        self.assertFalse(refine["allowed"])
        self.assertIn("深度闭关", refine["reason"])
        self.assertEqual(now + yinluo.YINLUO_PHASEFUL_RISK_RETRY_SEC, refine["retry_after"])
        self.assertFalse(collect["allowed"])
        self.assertIn("暂不发送阴罗主动命令", collect["reason"])

    def test_summon_blocks_below_jiedan_even_with_recent_observation(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.update_send_as_profile(self.identity_id, realm="筑基后期", xiuwei_max=30000)
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "last_action": "阴罗幡",
                "last_result": "panel",
                "next_demon_summon_time": 0,
                "next_blood_forest_time": now + 3600,
                "ready_slots": 0,
            }

            plan = yinluo.build_yinluo_manual_plan("demon_summon", now=now)

        self.assertFalse(plan["allowed"])
        self.assertEqual(".召唤魔影", plan["command"])
        self.assertIn("需结丹期", plan["reason"])
        self.assertIn("筑基后期", plan["reason"])

    def test_blood_forest_is_controlled_by_recent_observation_and_cooldown(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "last_action": "血洗山林",
                "last_result": "success",
                "next_blood_forest_time": 0,
            }
            due = yinluo.build_yinluo_manual_plan("血洗山林", now=now)

            state_module.state["yinluo_observation"]["next_blood_forest_time"] = now + 600
            cooldown = yinluo.build_yinluo_manual_plan("血洗山林", now=now)

            state_module.state["yinluo_observation"]["next_blood_forest_time"] = 0
            state_module.state["yinluo_observation"]["last_result"] = "pending"
            pending = yinluo.build_yinluo_manual_plan("血洗山林", now=now)

            curse = yinluo.build_yinluo_manual_plan("下咒", now=now)
            possess = yinluo.build_yinluo_manual_plan("夺舍", now=now)

        self.assertTrue(due["allowed"])
        self.assertEqual(".血洗山林", due["command"])
        self.assertEqual("yinluo_blood_forest", due["family"])
        self.assertFalse(cooldown["allowed"])
        self.assertIn("冷却", cooldown["reason"])
        self.assertFalse(pending["allowed"])
        self.assertIn("结算中", pending["reason"])
        self.assertFalse(curse["allowed"])
        self.assertFalse(possess["allowed"])
        self.assertIn("只观察/人工处理", curse["reason"])
        self.assertIn("只观察/人工处理", possess["reason"])

    def test_recent_not_member_observation_blocks_active_actions(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "last_action": "血洗山林",
                "last_result": "not_member",
                "last_error": "不懂此等杀伐之术",
                "next_blood_forest_time": 0,
            }
            banner = yinluo.build_yinluo_manual_plan("banner", now=now)
            blood = yinluo.build_yinluo_manual_plan("血洗山林", now=now)
            summon = yinluo.build_yinluo_manual_plan("召唤", now=now)

        self.assertTrue(banner["allowed"])
        self.assertFalse(blood["allowed"])
        self.assertFalse(summon["allowed"])
        self.assertIn("并非阴罗宗弟子", blood["reason"])


class YinluoSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        self.identity_id = 3102
        state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(
            self.identity_id,
            username="yinluo_auto",
            label="yinluo_auto",
            sect_name="阴罗宗",
            realm="结丹初期",
            xiuwei_max=50000,
        )

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    async def _run_with_observation(self, observation, now=1_780_000_000.0):
        msg = SimpleNamespace(id=9201, sent_at=now)
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = observation
            with patch.object(yinluo, "save_state"), patch.object(yinluo, "send_game_command", return_value=msg) as send_mock:
                await yinluo.run_yinluo_scheduler(now)
            return send_mock, state_module.state["yinluo_observation"]

    async def test_scheduler_queries_banner_when_observation_missing(self):
        send_mock, observed = await self._run_with_observation({})

        send_mock.assert_awaited_once()
        self.assertEqual(".我的阴罗幡", send_mock.await_args.args[0])
        self.assertEqual("阴罗宗", send_mock.await_args.kwargs["source_module"])
        self.assertEqual("banner", observed["auto_last_action"])

    async def test_scheduler_collects_one_ready_slot_and_keeps_remaining_hint(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "水镜真人",
            "banner_name": "灭法幡",
            "ready_slots": 2,
            "ready_slot_numbers": [1, 4],
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".收取精华 1", send_mock.await_args.args[0])
        self.assertEqual("collect", observed["auto_last_action"])
        self.assertEqual(1, observed["ready_slots"])
        self.assertEqual([4], observed["ready_slot_numbers"])
        self.assertEqual([1], observed["auto_collect_pending"]["slots"])

    async def test_scheduler_skips_collect_blocked_ready_slots(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "灭法幡胚",
            "ready_slots": 2,
            "ready_slot_numbers": [2, 4],
            "ready_slots_detail": [
                {"slot": 2, "target": "妖兽精魄"},
                {"slot": 4, "target": "凶兽戾魄"},
            ],
            "collect_blocked_slots": {
                "2": {"until": now + 7 * 86400, "target": "妖兽精魄", "reason": "测试保护"},
            },
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_config": {
                "collect": True,
                "refine": False,
                "blood_forest": False,
                "demon_summon": False,
                "convert": False,
                "refine_targets": [],
            },
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".收取精华 4", send_mock.await_args.args[0])
        self.assertEqual([2], observed["collect_blocked_ready_slot_numbers"])
        self.assertEqual([], observed["ready_slot_numbers"])
        self.assertEqual([4], observed["auto_collect_pending"]["slots"])

    async def test_scheduler_does_not_collect_when_all_ready_slots_are_blocked(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "灭法幡胚",
            "ready_slots": 1,
            "ready_slot_numbers": [2],
            "ready_slots_detail": [{"slot": 2, "target": "妖兽精魄"}],
            "collect_blocked_slots": {
                "2": {"until": now + 7 * 86400, "target": "妖兽精魄", "reason": "测试保护"},
            },
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_config": {
                "collect": True,
                "refine": False,
                "blood_forest": False,
                "demon_summon": False,
                "convert": False,
                "refine_targets": [],
            },
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_not_called()
        self.assertEqual("idle", observed["auto_last_action"])
        self.assertIn("收取保护期", observed["auto_last_error"])

    async def test_scheduler_waits_for_collect_reply_before_next_ready_slot(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "水镜真人",
            "banner_name": "灭法幡",
            "ready_slots": 1,
            "ready_slot_numbers": [4],
            "auto_collect_pending": {"slots": [1], "sent_at": now - 30},
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_not_called()
        self.assertEqual("collect_pending", observed["auto_last_action"])
        self.assertIn("等待真实回复", observed["auto_last_error"])

    async def test_scheduler_queries_banner_when_collect_reply_times_out(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "水镜真人",
            "banner_name": "灭法幡",
            "ready_slots": 1,
            "ready_slot_numbers": [4],
            "auto_collect_pending": {"slots": [1], "sent_at": now - yinluo.YINLUO_AUTO_COLLECT_CONFIRM_TIMEOUT_SEC - 1},
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".我的阴罗幡", send_mock.await_args.args[0])
        self.assertEqual("banner", observed["auto_last_action"])
        self.assertIn("收取精华等待回复超时", observed["auto_calibrate_reason"])

    async def test_scheduler_waits_for_refine_reply_before_calibration(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "水镜真人",
            "banner_name": "灭法幡",
            "empty_slots": 1,
            "empty_slot_numbers": [4],
            "refining_slots": 1,
            "refining_slot_numbers": [3],
            "soul_stocks": {"凶兽戾魄": 0},
            "auto_refine_pending": {
                "slot": 3,
                "target": "凶兽戾魄",
                "sent_at": now - 30,
                "pre_empty_slot_numbers": [3, 4],
                "pre_refining_slot_numbers": [],
                "pre_empty_slots": 2,
                "pre_refining_slots": 0,
                "pre_sha_current": 1300,
                "pre_sha_percent": 8,
                "pre_soul_stocks": {"凶兽戾魄": 1},
            },
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_not_called()
        self.assertEqual("refine_pending", observed["auto_last_action"])
        self.assertIn("等待真实回复", observed["auto_last_error"])
        self.assertEqual(3, observed["auto_refine_pending"]["slot"])

    async def test_scheduler_queries_banner_when_refine_reply_times_out(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "水镜真人",
            "banner_name": "灭法幡",
            "sha_current": 300,
            "sha_max": 15000,
            "sha_percent": 2,
            "empty_slots": 1,
            "empty_slot_numbers": [4],
            "refining_slots": 1,
            "refining_slot_numbers": [3],
            "soul_stocks": {"凶兽戾魄": 0},
            "auto_refine_pending": {
                "slot": 3,
                "target": "凶兽戾魄",
                "sent_at": now - yinluo.YINLUO_AUTO_REFINE_CONFIRM_TIMEOUT_SEC - 1,
                "pre_empty_slot_numbers": [3, 4],
                "pre_refining_slot_numbers": [],
                "pre_empty_slots": 2,
                "pre_refining_slots": 0,
                "pre_sha_current": 1300,
                "pre_sha_percent": 8,
                "pre_soul_stocks": {"凶兽戾魄": 1},
            },
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".我的阴罗幡", send_mock.await_args.args[0])
        self.assertNotEqual(".囚禁魂魄 3 凶兽戾魄", send_mock.await_args.args[0])
        self.assertEqual("banner", observed["auto_last_action"])
        self.assertEqual({}, observed["auto_refine_pending"])
        self.assertEqual([3, 4], observed["empty_slot_numbers"])
        self.assertEqual([], observed["refining_slot_numbers"])
        self.assertEqual(1, observed["soul_stocks"]["凶兽戾魄"])
        self.assertIn("囚禁魂魄等待回复超时", observed["auto_calibrate_reason"])

    async def test_scheduler_respects_collect_auto_toggle(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "水镜真人",
            "banner_name": "灭法幡",
            "ready_slots": 1,
            "ready_slot_numbers": [1],
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_config": {
                "collect": False,
                "refine": False,
                "blood_forest": False,
                "demon_summon": False,
                "convert": False,
                "refine_targets": ["凶兽戾魄"],
            },
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_not_called()
        self.assertEqual("idle", observed["auto_last_action"])

    async def test_manual_collect_marks_slot_pending_before_auto_can_repeat(self):
        now = 1_780_000_000.0
        msg = SimpleNamespace(id=9301, sent_at=now)
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "banner_owner": "缘初子",
                "banner_name": "血煞幡胚",
                "ready_slots": 2,
                "ready_slot_numbers": [1, 2],
                "next_blood_forest_time": now + 3600,
                "next_demon_summon_time": now + 3600,
            }
            with patch.object(yinluo, "save_state"), patch.object(yinluo, "send_game_command", return_value=msg) as send_mock:
                ok, message, plan = await yinluo.execute_yinluo_manual_action("collect", "1", now=now)
            observed = state_module.state["yinluo_observation"]

        self.assertTrue(ok, message)
        self.assertEqual(".收取精华 1", plan["command"])
        send_mock.assert_awaited_once()
        self.assertEqual([2], observed["ready_slot_numbers"])
        self.assertEqual([1], observed["auto_collect_pending"]["slots"])

    async def test_manual_collect_blocks_while_collect_pending(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "banner_owner": "缘初子",
                "banner_name": "血煞幡胚",
                "ready_slots": 1,
                "ready_slot_numbers": [2],
                "auto_collect_pending": {"slots": [1], "sent_at": now - 30},
            }
            with patch.object(yinluo, "send_game_command") as send_mock:
                ok, message, plan = await yinluo.execute_yinluo_manual_action("collect", "2", now=now)

        self.assertFalse(ok)
        self.assertIn("等待真实回复", message)
        self.assertFalse(plan["allowed"])
        send_mock.assert_not_called()

    async def test_manual_collect_blocks_protected_slot(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "banner_owner": "缘初子",
                "ready_slots": 1,
                "ready_slot_numbers": [2],
                "ready_slots_detail": [{"slot": 2, "target": "妖兽精魄"}],
                "collect_blocked_slots": {
                    "2": {"until": now + 7 * 86400, "target": "妖兽精魄", "reason": "测试保护"},
                },
            }
            with patch.object(yinluo, "send_game_command") as send_mock:
                ok, message, plan = await yinluo.execute_yinluo_manual_action("collect", "2", now=now)

        self.assertFalse(ok)
        self.assertIn("收取保护", message)
        self.assertFalse(plan["allowed"])
        send_mock.assert_not_called()

    async def test_manual_daily_sacrifice_marks_daily_cooldown_after_send(self):
        now = 1_780_000_000.0
        msg = SimpleNamespace(id=9305, sent_at=now)
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "banner_owner": "缘初子",
                "banner_name": "血煞幡胚",
                "next_daily_sacrifice_time": 0,
            }
            with patch.object(yinluo, "save_state"), patch.object(yinluo, "send_game_command", return_value=msg) as send_mock:
                ok, message, plan = await yinluo.execute_yinluo_manual_action("献祭", now=now)
            observed = state_module.state["yinluo_observation"]

        self.assertTrue(ok, message)
        self.assertEqual(".每日献祭", plan["command"])
        send_mock.assert_awaited_once()
        self.assertEqual(yinluo.get_day_key(now), observed["last_daily_sacrifice_day"])
        self.assertGreater(observed["next_daily_sacrifice_time"], now)
        self.assertEqual("daily_sacrifice", observed["auto_last_action"])

    async def test_scheduler_auto_daily_sacrifice_when_enabled_and_due(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "血煞幡胚",
            "ready_slots": 0,
            "next_daily_sacrifice_time": 0,
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_config": {
                "collect": True,
                "daily_sacrifice": True,
                "refine": False,
                "blood_forest": False,
                "demon_summon": False,
                "convert": False,
                "refine_targets": [],
            },
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".每日献祭", send_mock.await_args.args[0])
        self.assertEqual("daily_sacrifice", observed["auto_last_action"])
        self.assertEqual(yinluo.get_day_key(now), observed["last_daily_sacrifice_day"])
        self.assertGreater(observed["next_daily_sacrifice_time"], now)

    async def test_scheduler_auto_refines_when_slot_stock_and_sha_are_known(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "血煞幡胚",
            "sha_current": 1300,
            "sha_max": 15000,
            "empty_slots": 2,
            "empty_slot_numbers": [3, 4],
            "ready_slots": 0,
            "soul_stocks": {"妖兽精魄": 14, "凶兽戾魄": 1},
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_config": {
                "collect": True,
                "refine": True,
                "blood_forest": False,
                "demon_summon": False,
                "convert": False,
                "refine_targets": ["凶兽戾魄"],
            },
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".囚禁魂魄 3 凶兽戾魄", send_mock.await_args.args[0])
        self.assertEqual("refine", observed["auto_last_action"])
        self.assertEqual([4], observed["empty_slot_numbers"])
        self.assertEqual(0, observed["soul_stocks"]["凶兽戾魄"])
        self.assertEqual(300, observed["sha_current"])
        self.assertEqual(now + yinluo.YINLUO_AUTO_CHAIN_STEP_SEC, observed["auto_next_time"])
        self.assertEqual(3, observed["auto_refine_pending"]["slot"])

    async def test_scheduler_auto_refine_uses_lowest_empty_slot_number(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "血煞幡胚",
            "sha_current": 1300,
            "sha_max": 15000,
            "empty_slots": 3,
            "empty_slot_numbers": [6, 4, 5],
            "ready_slots": 0,
            "soul_stocks": {"凶兽戾魄": 1},
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_config": {
                "collect": True,
                "refine": True,
                "blood_forest": False,
                "demon_summon": False,
                "convert": False,
                "refine_targets": ["凶兽戾魄"],
            },
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".囚禁魂魄 4 凶兽戾魄", send_mock.await_args.args[0])
        self.assertEqual(4, observed["auto_refine_pending"]["slot"])

    async def test_scheduler_auto_soothes_lowest_exhausted_slot(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "灭法幡",
            "ready_slots": 0,
            "exhausted_slot_numbers": [5, 3],
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_config": {
                "collect": True,
                "refine": True,
                "soothe": True,
                "blood_forest": False,
                "demon_summon": False,
                "convert": False,
                "refine_targets": ["凶兽戾魄"],
            },
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".安抚幡灵 3", send_mock.await_args.args[0])
        self.assertEqual("soothe", observed["auto_last_action"])
        self.assertEqual({"slot": 3, "sent_at": now}, observed["auto_soothe_pending"])
        self.assertEqual([5], observed["exhausted_slot_numbers"])

    async def test_scheduler_waits_for_soothe_reply_before_repeat(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "灭法幡",
            "ready_slots": 0,
            "exhausted_slot_numbers": [5],
            "auto_soothe_pending": {"slot": 3, "sent_at": now - 30},
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_not_called()
        self.assertEqual("soothe_pending", observed["auto_last_action"])
        self.assertIn("等待真实回复", observed["auto_last_error"])
        self.assertEqual(3, observed["auto_soothe_pending"]["slot"])

    def test_soothe_success_clears_pending_and_requests_banner_calibration(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "banner_owner": "缘初子",
                "banner_name": "灭法幡",
                "exhausted_slot_numbers": [3],
                "auto_soothe_pending": {"slot": 3, "sent_at": now - 10},
            }
            self.assertTrue(yinluo.apply_yinluo_passive(
                "安抚成功！\n你消耗了 50 点修为，成功安抚了 1 个炼化槽。",
                now=now,
            ))
            observed = state_module.state["yinluo_observation"]

        self.assertEqual("安抚幡灵", observed["last_action"])
        self.assertEqual({}, observed["auto_soothe_pending"])
        self.assertEqual([], observed["exhausted_slot_numbers"])
        self.assertIn(3, observed["refining_slot_numbers"])
        self.assertIn("查幡确认", observed["auto_calibrate_reason"])

    async def test_scheduler_does_not_auto_refine_without_selected_targets(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "血煞幡胚",
            "sha_current": 1300,
            "sha_max": 15000,
            "empty_slots": 2,
            "empty_slot_numbers": [3, 4],
            "ready_slots": 0,
            "soul_stocks": {"妖兽精魄": 14, "凶兽戾魄": 1},
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_config": {
                "collect": True,
                "refine": True,
                "blood_forest": False,
                "demon_summon": False,
                "convert": False,
                "refine_targets": [],
            },
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_not_called()
        self.assertEqual("idle", observed["auto_last_action"])

    async def test_scheduler_rechecks_banner_when_refining_slot_prediction_is_due(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "血煞幡胚",
            "ready_slots": 0,
            "refining_slots": 1,
            "refining_slot_numbers": [3],
            "refining_slots_detail": [{"slot": 3, "target": "凶兽戾魄", "finish_time": now - 1}],
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_config": {
                "collect": True,
                "refine": False,
                "blood_forest": False,
                "demon_summon": False,
                "convert": False,
                "refine_targets": [],
            },
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".我的阴罗幡", send_mock.await_args.args[0])
        self.assertEqual("banner", observed["auto_last_action"])
        self.assertIn("预计到期", observed["auto_calibrate_reason"])
        self.assertEqual({}, observed["auto_collect_pending"])
        self.assertEqual([3], observed["refining_slot_numbers"])
        self.assertEqual(1, observed["refining_slots"])
        self.assertEqual(now + yinluo.YINLUO_AUTO_CALIBRATE_RETRY_SEC, observed["auto_next_time"])

    async def test_scheduler_auto_converts_only_when_enabled_and_refine_needs_sha(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "血煞幡胚",
            "sha_current": 300,
            "sha_max": 15000,
            "empty_slots": 1,
            "empty_slot_numbers": [3],
            "ready_slots": 0,
            "soul_stocks": {"凶兽戾魄": 1},
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "next_convert_time": 0,
            "auto_config": {
                "collect": True,
                "refine": True,
                "blood_forest": False,
                "demon_summon": False,
                "convert": True,
                "convert_amount": 10000,
                "refine_targets": ["凶兽戾魄"],
            },
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".化功为煞 10000", send_mock.await_args.args[0])
        self.assertEqual("convert", observed["auto_last_action"])
        self.assertGreater(observed["next_convert_time"], now)

    async def test_scheduler_blocks_auto_convert_when_known_xiuwei_is_insufficient(self):
        now = 1_780_000_000.0
        state_module.update_send_as_profile(self.identity_id, xiuwei_current=800, xiuwei_max=50000)
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "血煞幡胚",
            "sha_current": 300,
            "sha_max": 15000,
            "empty_slots": 1,
            "empty_slot_numbers": [3],
            "ready_slots": 0,
            "soul_stocks": {"凶兽戾魄": 1},
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "next_convert_time": 0,
            "auto_config": {
                "collect": True,
                "refine": True,
                "blood_forest": False,
                "demon_summon": False,
                "convert": True,
                "convert_amount": 10000,
                "refine_targets": ["凶兽戾魄"],
            },
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_not_called()
        self.assertEqual("idle", observed["auto_last_action"])

    async def test_scheduler_does_not_refine_without_known_empty_slot_number(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "血煞幡胚",
            "sha_current": 1300,
            "sha_max": 15000,
            "empty_slots": 2,
            "ready_slots": 0,
            "soul_stocks": {"凶兽戾魄": 1},
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_config": {
                "collect": True,
                "refine": True,
                "blood_forest": False,
                "demon_summon": False,
                "convert": False,
                "refine_targets": ["凶兽戾魄"],
            },
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_not_called()
        self.assertEqual("idle", observed["auto_last_action"])

    async def test_auto_refine_failure_rolls_back_and_requires_banner_calibration(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "血煞幡胚",
            "sha_current": 1300,
            "sha_max": 15000,
            "empty_slots": 1,
            "empty_slot_numbers": [3],
            "ready_slots": 0,
            "soul_stocks": {"凶兽戾魄": 1},
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_config": {
                "collect": True,
                "refine": True,
                "blood_forest": False,
                "demon_summon": False,
                "convert": False,
                "refine_targets": ["凶兽戾魄"],
            },
            "auto_next_time": now - 1,
        }, now=now)
        send_mock.assert_awaited_once()
        self.assertEqual(".囚禁魂魄 3 凶兽戾魄", send_mock.await_args.args[0])

        with state_module.use_identity(self.identity_id):
            self.assertTrue(yinluo.apply_yinluo_passive("你的煞气不足！炼化需要消耗 1000 点煞气。", now=now + 1))
            observed = state_module.state["yinluo_observation"]

        self.assertEqual([3], observed["empty_slot_numbers"])
        self.assertEqual([], observed["refining_slot_numbers"])
        self.assertEqual(1300, observed["sha_current"])
        self.assertEqual(1, observed["soul_stocks"]["凶兽戾魄"])
        self.assertEqual({}, observed["auto_refine_pending"])
        self.assertIn("囚禁魂魄失败", observed["auto_calibrate_reason"])

    async def test_scheduler_queries_banner_before_refine_when_calibration_required(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "缘初子",
            "banner_name": "血煞幡胚",
            "sha_current": 1300,
            "sha_max": 15000,
            "empty_slots": 1,
            "empty_slot_numbers": [3],
            "ready_slots": 0,
            "soul_stocks": {"凶兽戾魄": 1},
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_calibrate_reason": "囚禁魂魄失败，需查幡校准。",
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".我的阴罗幡", send_mock.await_args.args[0])
        self.assertEqual("banner", observed["auto_last_action"])

    async def test_scheduler_summons_demon_only_after_banner_hint_and_due(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "水镜真人",
            "banner_name": "灭法幡",
            "ready_slots": 0,
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": 0,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".召唤魔影", send_mock.await_args.args[0])
        self.assertEqual("demon_summon", observed["auto_last_action"])
        self.assertGreater(observed["next_demon_summon_time"], now)

    async def test_scheduler_sends_blood_forest_when_due_before_demon_summon(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "水镜真人",
            "banner_name": "灭法幡",
            "ready_slots": 0,
            "next_blood_forest_time": 0,
            "next_demon_summon_time": now + 3600,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".血洗山林", send_mock.await_args.args[0])
        self.assertEqual("blood_forest", observed["auto_last_action"])
        self.assertGreater(observed["next_blood_forest_time"], now)

    async def test_scheduler_keeps_due_demon_summon_on_short_chain_after_blood_forest(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "水镜真人",
            "banner_name": "灭法幡",
            "ready_slots": 0,
            "next_blood_forest_time": 0,
            "next_demon_summon_time": 0,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".血洗山林", send_mock.await_args.args[0])
        self.assertEqual("blood_forest", observed["auto_last_action"])
        self.assertEqual(now + yinluo.YINLUO_AUTO_CHAIN_STEP_SEC, observed["auto_next_time"])

    async def test_scheduler_waits_for_pending_resolution_before_any_followup(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "last_action": "血洗山林",
            "last_result": "pending",
            "banner_owner": "水镜真人",
            "banner_name": "灭法幡",
            "ready_slots": 1,
            "ready_slot_numbers": [1],
            "next_blood_forest_time": 0,
            "next_demon_summon_time": 0,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_not_called()
        self.assertEqual("pending", observed["auto_last_action"])
        self.assertIn("结算中", observed["auto_last_error"])
        self.assertEqual(now + yinluo.YINLUO_AUTO_CHAIN_STEP_SEC, observed["auto_next_time"])

    async def test_scheduler_rechecks_banner_for_non_member_like_state_instead_of_high_risk_actions(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "last_action": "血洗山林",
            "last_result": "not_member",
            "ready_slots": 0,
            "next_demon_summon_time": 0,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".我的阴罗幡", send_mock.await_args.args[0])
        self.assertEqual("banner", observed["auto_last_action"])

    async def test_scheduler_respects_cooldown_and_future_auto_time(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["yinluo_enabled"] = True
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 60,
                "banner_owner": "水镜真人",
                "banner_name": "灭法幡",
                "ready_slots": 0,
                "next_blood_forest_time": now + 3600,
                "next_demon_summon_time": now + 3600,
                "auto_next_time": now + 300,
            }
            with patch.object(yinluo, "send_game_command") as send_mock:
                await yinluo.run_yinluo_scheduler(now)

            send_mock.assert_not_called()


class YinluoAppWiringTests(unittest.TestCase):
    def test_yinluo_scheduler_is_wired_into_identity_scheduler_loop(self):
        ordinary = app.get_identity_scheduler_order_contract()["ordinary"]

        self.assertIn("run_yinluo_scheduler", ordinary)


class YinluoPassiveInboxTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._stats_snapshot = copy.deepcopy(passive_inbox._passive_stats)
        self._observed_snapshot = dict(passive_inbox._observed_passive_events)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        passive_inbox._passive_stats = {
            "total": 0,
            "changed": 0,
            "skipped": 0,
            "modules": {},
            "skip_reasons": {},
            "recent": [],
        }
        passive_inbox._observed_passive_events = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        passive_inbox._passive_stats = self._stats_snapshot
        passive_inbox._observed_passive_events = self._observed_snapshot

    def _prepare_identity(self, send_as_id=3001, username="yinluo_user"):
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(
            send_as_id,
            username=username,
            label=username,
            sect_name="阴罗宗",
        )
        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_enabled"] = True
        return send_as_id

    def test_apply_blood_forest_cooldown_tracks_next_time_and_due_followup(self):
        now = 1_779_450_000.0
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "auto_next_time": now + yinluo.YINLUO_AUTO_STATUS_BACKOFF_SEC,
            }
            changed = yinluo.apply_yinluo_passive(real_text("yinluo.blood_forest.cooldown"), now=now)
            observed = state_module.state["yinluo_observation"]

        self.assertTrue(changed)
        self.assertEqual("血洗山林", observed["last_action"])
        self.assertEqual("cooldown", observed["last_result"])
        self.assertEqual(now + 3 * 3600 + 59 * 60 + 20 + yinluo.YINLUO_TIME_BUFFER_SEC, observed["next_blood_forest_time"])
        self.assertEqual(now + yinluo.YINLUO_AUTO_CHAIN_STEP_SEC, observed["auto_next_time"])

    def test_apply_success_replies_update_soul_stock_and_daily_sacrifice(self):
        now = 1_779_450_000.0
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 30,
                "banner_owner": "缘初子",
                "sha_current": 800,
                "sha_max": 15000,
                "soul_stocks": {"妖兽精魄": 13},
                "empty_slots": 2,
                "empty_slot_numbers": [1, 2],
            }
            self.assertTrue(yinluo.apply_yinluo_passive(real_text("yinluo.blood_forest.success"), now=now))
            self.assertTrue(yinluo.apply_yinluo_passive(real_text("yinluo.demon_summon.success"), now=now + 1))
            self.assertTrue(yinluo.apply_yinluo_passive("你引动九幽煞气灌入幡中，阴罗幡发出一阵愉悦的嘶鸣！你的煞气池增加了 500 点。", now=now + 2))
            observed = state_module.state["yinluo_observation"]

        self.assertEqual(14, observed["soul_stocks"]["妖兽精魄"])
        self.assertEqual(1, observed["soul_stocks"]["凶兽戾魄"])
        self.assertEqual(1300, observed["sha_current"])
        self.assertEqual(0, observed["next_convert_time"])
        self.assertEqual(yinluo.get_day_key(now + 2), observed["last_daily_sacrifice_day"])
        self.assertGreater(observed["next_daily_sacrifice_time"], now + 2)
        self.assertEqual("每日献祭", observed["last_action"])

    def test_daily_sacrifice_uses_stable_event_key_and_original_day_hint(self):
        now = 1_779_408_030.0
        send_as_id = self._prepare_identity()
        text = "你引动九幽煞气灌入幡中，阴罗幡发出一阵愉悦的嘶鸣！你的煞气池增加了 500 点。"

        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 30,
                "banner_owner": "缘初子",
                "sha_current": 800,
                "sha_max": 15000,
                "last_daily_sacrifice_day": yinluo.get_day_key(now - 180),
            }
            state_module.state["my_msg_ids"] = {8954050: now - 180}
            context = {
                "identity_id": send_as_id,
                "chat_id": -1001680975844,
                "msg_id": 8954051,
                "reply_to_msg_id": 8954050,
                "root_msg_id": 8954050,
            }
            self.assertTrue(yinluo.apply_yinluo_passive(text, now=now, family="yinluo_daily_sacrifice", event_context=context))
            observed = state_module.state["yinluo_observation"]
            first_key = observed["last_daily_sacrifice_result_key"]
            self.assertTrue(first_key.startswith("daily_sacrifice:ids:"))
            self.assertEqual(yinluo.get_day_key(now - 180), observed["last_daily_sacrifice_day"])
            self.assertEqual(1300, observed["sha_current"])

            replay_context = dict(context)
            replay_context["msg_id"] = 8954051
            self.assertTrue(yinluo.apply_yinluo_passive(text, now=now + 60, family="yinluo_daily_sacrifice", event_context=replay_context))
            observed = state_module.state["yinluo_observation"]

        self.assertEqual(first_key, observed["last_daily_sacrifice_result_key"])
        self.assertEqual(1300, observed["sha_current"])

    def test_apply_convert_success_deducts_known_profile_xiuwei(self):
        now = 1_779_450_000.0
        send_as_id = self._prepare_identity()
        state_module.update_send_as_profile(send_as_id, xiuwei_current=12000, xiuwei_max=50000)

        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 30,
                "banner_owner": "缘初子",
                "sha_current": 300,
                "sha_max": 15000,
            }
            changed = yinluo.apply_yinluo_passive("【转化成功】\n你成功将 10000 点修为炼化，煞气池增加了 2000 点！", now=now)
            changed_again = yinluo.apply_yinluo_passive(
                "【转化成功】\n你成功将 10000 点修为炼化，煞气池增加了 2000 点！",
                now=now + 30,
            )
            observed = state_module.state["yinluo_observation"]
            profile = state_module.get_send_as_profile(send_as_id)

        self.assertTrue(changed)
        self.assertTrue(changed_again)
        self.assertEqual(2300, observed["sha_current"])
        self.assertEqual(now + yinluo.YINLUO_CONVERT_OBSERVED_CD_SEC + yinluo.YINLUO_TIME_BUFFER_SEC, observed["next_convert_time"])
        self.assertEqual(2000, profile["xiuwei_current"])

    def test_apply_refine_success_is_idempotent_after_auto_sent_marker(self):
        now = 1_779_450_000.0
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 30,
                "banner_owner": "缘初子",
                "sha_current": 300,
                "sha_max": 15000,
                "soul_stocks": {"凶兽戾魄": 0},
                "empty_slots": 1,
                "empty_slot_numbers": [4],
                "refining_slots": 1,
                "refining_slot_numbers": [3],
            }
            self.assertTrue(yinluo.apply_yinluo_passive(
                "一缕【凶兽戾魄】被强行打入3号炼化槽，在煞气的包裹下发出阵阵哀嚎，炼化已开始。",
                now=now,
            ))
            observed = state_module.state["yinluo_observation"]

        self.assertEqual(300, observed["sha_current"])
        self.assertEqual(0, observed["soul_stocks"]["凶兽戾魄"])
        self.assertEqual([4], observed["empty_slot_numbers"])
        self.assertEqual([3], observed["refining_slot_numbers"])

    def test_apply_empty_collect_result_requires_banner_calibration(self):
        now = 1_779_450_000.0
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 30,
                "banner_owner": "缘初子",
                "ready_slots": 2,
                "ready_slot_numbers": [1, 2],
                "auto_collect_pending": {"slots": [1], "sent_at": now - 10},
            }
            self.assertTrue(yinluo.apply_yinluo_passive(
                "收取成功！\n你从 0 个炼化槽中获得了: ！",
                now=now,
            ))
            observed = state_module.state["yinluo_observation"]

        self.assertEqual("empty", observed["last_result"])
        self.assertEqual(0, observed["ready_slots"])
        self.assertEqual([], observed["ready_slot_numbers"])
        self.assertEqual({}, observed["auto_collect_pending"])
        self.assertIn("收取精华空结果", observed["auto_calibrate_reason"])

    def test_apply_refine_slot_busy_restores_pending_and_requires_calibration(self):
        now = 1_779_450_000.0
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "last_observed_at": now - 30,
                "banner_owner": "缘初子",
                "sha_current": 300,
                "sha_max": 15000,
                "sha_percent": 2,
                "soul_stocks": {"凶兽戾魄": 0},
                "empty_slots": 0,
                "empty_slot_numbers": [],
                "refining_slots": 1,
                "refining_slot_numbers": [3],
                "auto_refine_pending": {
                    "slot": 3,
                    "target": "凶兽戾魄",
                    "sent_at": now - 10,
                    "pre_empty_slot_numbers": [3],
                    "pre_refining_slot_numbers": [],
                    "pre_empty_slots": 1,
                    "pre_refining_slots": 0,
                    "pre_sha_current": 500,
                    "pre_sha_percent": 3,
                    "pre_soul_stocks": {"凶兽戾魄": 1},
                },
            }
            self.assertTrue(yinluo.apply_yinluo_passive(
                "此炼化槽正在运转中，无法囚禁新的魂魄。",
                now=now,
                family="yinluo_refine",
            ))
            observed = state_module.state["yinluo_observation"]

        self.assertEqual("slot_busy", observed["last_result"])
        self.assertEqual(500, observed["sha_current"])
        self.assertEqual(1, observed["soul_stocks"]["凶兽戾魄"])
        self.assertEqual([3], observed["empty_slot_numbers"])
        self.assertEqual([], observed["refining_slot_numbers"])
        self.assertEqual({}, observed["auto_refine_pending"])
        self.assertIn("炼化槽正在运转中", observed["auto_calibrate_reason"])

    def test_apply_zero_second_banner_keeps_short_recheck_timer(self):
        now = 1_781_443_187.0
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "auto_next_time": now + yinluo.YINLUO_AUTO_STATUS_BACKOFF_SEC,
                "next_blood_forest_time": now + 4 * 3600,
                "next_demon_summon_time": now + 8 * 3600,
            }
            self.assertTrue(yinluo.apply_yinluo_passive(
                "【缘初子的阴罗幡】\n"
                "煞气池: 2140 / 15000 (14%)\n"
                "魂魄储备:\n"
                " - 妖兽精魄: 15 缕\n\n"
                "炼化槽:\n"
                "3号槽: [炼化中] - 凶兽戾魄 (剩余: 0秒)\n"
                "6号槽: [空闲]",
                now=now,
            ))
            observed = state_module.state["yinluo_observation"]

        self.assertEqual("阴罗幡", observed["last_action"])
        self.assertEqual([], observed["ready_slot_numbers"])
        self.assertEqual(now + yinluo.YINLUO_REFINING_DUE_RECHECK_SEC, observed["auto_next_time"])

    def test_passive_inbox_updates_yinluo_from_reply_context(self):
        send_as_id = self._prepare_identity()
        event = SimpleNamespace(chat_id=-1001680975844, id=8954045)

        with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
            handled = asyncio.run(passive_inbox.handle_passive_module_card(
                real_text("yinluo.banner.basic"),
                now=1_779_450_000.0,
                reply_context={
                    "send_as_id": send_as_id,
                    "family": "yinluo_banner",
                    "reply_to_msg_id": 8954042,
                    "root_msg_id": 8954042,
                },
                event=event,
                event_type="message",
            ))

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            observed = state_module.state["yinluo_observation"]
            self.assertEqual("阴罗幡", observed["last_action"])
            self.assertEqual(269465, observed["sha_current"])
            self.assertEqual(1, observed["ready_slots"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["changed"])
        self.assertEqual(1, snapshot["modules"]["yinluo"])

    def test_passive_inbox_applies_yinluo_refine_slot_busy_from_reply_context(self):
        send_as_id = self._prepare_identity()
        event = SimpleNamespace(chat_id=-1001680975844, id=8954053)

        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "last_observed_at": 1_779_450_000.0 - 30,
                "banner_owner": "缘初子",
                "empty_slot_numbers": [],
                "refining_slot_numbers": [3],
                "auto_refine_pending": {
                    "slot": 3,
                    "target": "凶兽戾魄",
                    "sent_at": 1_779_450_000.0 - 10,
                    "pre_empty_slot_numbers": [3],
                    "pre_refining_slot_numbers": [],
                    "pre_empty_slots": 1,
                    "pre_refining_slots": 0,
                    "pre_sha_current": 500,
                    "pre_sha_percent": 3,
                    "pre_soul_stocks": {"凶兽戾魄": 1},
                },
            }

        with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
            handled = asyncio.run(passive_inbox.handle_passive_module_card(
                "此炼化槽正在运转中，无法囚禁新的魂魄。",
                now=1_779_450_000.0,
                reply_context={
                    "send_as_id": send_as_id,
                    "family": "yinluo_refine",
                    "reply_to_msg_id": 8954052,
                    "root_msg_id": 8954052,
                },
                event=event,
                event_type="message",
            ))

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            observed = state_module.state["yinluo_observation"]
            self.assertEqual("slot_busy", observed["last_result"])
            self.assertEqual([3], observed["empty_slot_numbers"])
            self.assertEqual([], observed["refining_slot_numbers"])
            self.assertIn("炼化槽正在运转中", observed["auto_calibrate_reason"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["changed"])
        self.assertEqual(1, snapshot["modules"]["yinluo"])

    def test_passive_inbox_applies_yinluo_daily_sacrifice_from_reply_context(self):
        send_as_id = self._prepare_identity(username="yinluo_daily")
        event = SimpleNamespace(chat_id=-1001680975844, id=8954050)

        with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
            handled = asyncio.run(passive_inbox.handle_passive_module_card(
                real_text("yinluo.daily_sacrifice.success"),
                now=1_779_450_000.0,
                reply_context={
                    "send_as_id": send_as_id,
                    "family": "yinluo_daily_sacrifice",
                    "reply_to_msg_id": 8954049,
                    "root_msg_id": 8954049,
                },
                event=event,
                event_type="message",
            ))

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            observed = state_module.state["yinluo_observation"]
            self.assertEqual("每日献祭", observed["last_action"])
            self.assertEqual(575, observed["last_sha_gain"])
            self.assertEqual(75, observed["last_extra_sha_gain"])
            self.assertEqual(0, observed["sha_current"])
            self.assertEqual(yinluo.get_day_key(1_779_450_000.0), observed["last_daily_sacrifice_day"])
            self.assertGreater(observed["next_daily_sacrifice_time"], 1_779_450_000.0)
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["changed"])
        self.assertEqual(1, snapshot["modules"]["yinluo"])

    def test_passive_inbox_daily_sacrifice_dedupes_by_message_identity(self):
        send_as_id = self._prepare_identity(username="yinluo_daily_dedupe")
        text = "你引动九幽煞气灌入幡中，阴罗幡发出一阵愉悦的嘶鸣！你的煞气池增加了 500 点。"
        event = SimpleNamespace(chat_id=-1001680975844, id=8954052)

        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "last_observed_at": 1_779_407_900.0,
                "banner_owner": "缘初子",
                "sha_current": 800,
                "sha_max": 15000,
                "last_daily_sacrifice_day": yinluo.get_day_key(1_779_408_030.0 - 180),
            }
            state_module.state["my_msg_ids"] = {8954051: 1_779_408_030.0 - 180}

        with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
            handled = asyncio.run(passive_inbox.handle_passive_module_card(
                text,
                now=1_779_408_030.0,
                reply_context={
                    "send_as_id": send_as_id,
                    "family": "yinluo_daily_sacrifice",
                    "reply_to_msg_id": 8954051,
                    "root_msg_id": 8954051,
                },
                event=event,
                event_type="edit",
            ))
            duplicate_event = SimpleNamespace(chat_id=-1001680975844, id=8954052)
            handled_again = asyncio.run(passive_inbox.handle_passive_module_card(
                text + "\n ",
                now=1_779_408_090.0,
                reply_context={
                    "send_as_id": send_as_id,
                    "family": "yinluo_daily_sacrifice",
                    "reply_to_msg_id": 8954051,
                    "root_msg_id": 8954051,
                },
                event=duplicate_event,
                event_type="edit",
            ))

        self.assertTrue(handled)
        self.assertTrue(handled_again)
        with state_module.use_identity(send_as_id):
            observed = state_module.state["yinluo_observation"]
            self.assertEqual(1300, observed["sha_current"])
            self.assertEqual(yinluo.get_day_key(1_779_408_030.0 - 180), observed["last_daily_sacrifice_day"])

    def test_passive_inbox_routes_yinluo_banner_by_owner_name_without_reply_context(self):
        send_as_id = self._prepare_identity(username="yinluo_user")
        state_module.update_send_as_profile(send_as_id, label="水镜真人", daohao="水镜真人")
        event = SimpleNamespace(chat_id=-1001680975844, id=8954046)

        with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
            handled = asyncio.run(passive_inbox.handle_passive_module_card(
                real_text("yinluo.banner.basic"),
                now=1_779_450_000.0,
                reply_context=None,
                event=event,
                event_type="message",
            ))

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            observed = state_module.state["yinluo_observation"]
            self.assertEqual("水镜真人", observed["banner_owner"])
            self.assertEqual("灭法幡", observed["banner_name"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual("message:owner_name", snapshot["recent"][-1]["route_source"])

    def test_passive_inbox_classifies_unmatched_yinluo_owner_as_external(self):
        self._prepare_identity(username="yinluo_user")
        event = SimpleNamespace(chat_id=-1001680975844, id=8954047)

        with patch.object(passive_inbox, "_save_passive_stats"):
            handled = asyncio.run(passive_inbox.handle_passive_module_card(
                real_text("yinluo.banner.basic"),
                now=1_779_450_000.0,
                reply_context=None,
                event=event,
                event_type="message",
            ))

        self.assertFalse(handled)
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["skip_reasons"]["external_owner_no_match"])

    def test_passive_inbox_owner_hint_wins_over_body_at_mentions(self):
        send_as_id = self._prepare_identity(username="yinluo_user")
        event = SimpleNamespace(chat_id=-1001680975844, id=8954048)
        text = real_text("yinluo.banner.basic") + "\n旁注：@yinluo_user 曾协助炼幡。"

        with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
            handled = asyncio.run(passive_inbox.handle_passive_module_card(
                text,
                now=1_779_450_000.0,
                reply_context=None,
                event=event,
                event_type="message",
            ))

        self.assertFalse(handled)
        with state_module.use_identity(send_as_id):
            self.assertEqual({}, state_module.state["yinluo_observation"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["skip_reasons"]["external_owner_no_match"])

    def test_status_hides_stale_observation_when_module_disabled(self):
        send_as_id = self._prepare_identity(username="yinluo_user")
        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_enabled"] = False
            state_module.state["yinluo_observation"] = {
                "last_observed_at": 1_779_450_000.0,
                "last_action": "血洗山林",
                "last_error": "旧阴罗错误",
            }

            text = yinluo.get_yinluo_status_text()

        self.assertIn("模块：关闭", text)
        self.assertIn("不展示旧观察记录", text)
        self.assertNotIn("旧阴罗错误", text)
        self.assertNotIn("血洗山林", text)

    def test_real_message_fixture_includes_yinluo_samples(self):
        samples = list(iter_real_message_samples(FIXTURE_PATH, module="yinluo"))

        self.assertGreaterEqual(len(samples), 8)
        self.assertTrue(all(sample.family.startswith("yinluo_") for sample in samples))


class YinluoUiActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _prepare_identity(self, send_as_id=3301, *, sect_name="阴罗宗", enabled=True):
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(
            send_as_id,
            username=f"yinluo_ui_{send_as_id}",
            label=f"yinluo_ui_{send_as_id}",
            sect_name=sect_name,
            enabled=enabled,
        )
        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_enabled"] = True
        return send_as_id

    async def test_ui_blocks_non_yinluo_identity_before_sending(self):
        from model import ui

        send_as_id = self._prepare_identity(3301, sect_name="星宫")
        with patch.object(ui, "execute_yinluo_manual_action", new=AsyncMock(return_value=(True, "sent", {}))) as execute_mock:
            ok, message = await ui.ui_execute_yinluo_action(send_as_id, "banner")

        self.assertFalse(ok)
        self.assertIn("不可用", message)
        execute_mock.assert_not_awaited()

    async def test_ui_blocks_disabled_identity_before_sending(self):
        from model import ui

        send_as_id = self._prepare_identity(3302, sect_name="阴罗宗", enabled=False)
        with patch.object(ui, "execute_yinluo_manual_action", new=AsyncMock(return_value=(True, "sent", {}))) as execute_mock:
            ok, message = await ui.ui_execute_yinluo_action(send_as_id, "banner")

        self.assertFalse(ok)
        self.assertIn("停用", message)
        execute_mock.assert_not_awaited()

    async def test_ui_dispatches_available_yinluo_action(self):
        from model import ui

        send_as_id = self._prepare_identity(3303, sect_name="阴罗宗")
        with patch.object(ui, "execute_yinluo_manual_action", new=AsyncMock(return_value=(True, "sent", {}))) as execute_mock:
            ok, message = await ui.ui_execute_yinluo_action(send_as_id, "refine", "1 妖兽精魄")

        self.assertTrue(ok)
        self.assertIn("sent", message)
        execute_mock.assert_awaited_once_with("refine", "1 妖兽精魄", send_as_id=send_as_id)

    async def test_ui_dispatches_daily_sacrifice_action(self):
        from model import ui

        send_as_id = self._prepare_identity(3305, sect_name="阴罗宗")
        with patch.object(ui, "execute_yinluo_manual_action", new=AsyncMock(return_value=(True, "sent", {}))) as execute_mock:
            ok, message = await ui.ui_execute_yinluo_action(send_as_id, "daily_sacrifice")

        self.assertTrue(ok)
        self.assertIn("sent", message)
        execute_mock.assert_awaited_once_with("daily_sacrifice", "", send_as_id=send_as_id)

    def test_set_auto_config_same_value_does_not_save_observation(self):
        send_as_id = self._prepare_identity(3304, sect_name="阴罗宗")
        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = yinluo.normalize_yinluo_observation({
                "last_observed_at": 1_779_450_000.0,
                "auto_config": {
                    "collect": False,
                    "refine": True,
                    "blood_forest": False,
                    "demon_summon": True,
                    "convert": True,
                    "convert_amount": 10000,
                    "refine_targets": ["凶兽戾魄", "妖兽精魄"],
                },
            })
            before = copy.deepcopy(state_module.state["yinluo_observation"])
            with patch.object(yinluo, "save_state") as save_mock:
                ok, message, ui_state = yinluo.set_yinluo_auto_config({
                    "collect": False,
                    "refine": True,
                    "blood_forest": False,
                    "demon_summon": True,
                    "convert": True,
                    "convert_amount": 10000,
                    "refine_targets": "凶兽戾魄 妖兽精魄",
                })

            self.assertTrue(ok, message)
            self.assertEqual(10000, ui_state["auto_config"]["convert_amount"])
            self.assertEqual(before, state_module.state["yinluo_observation"])
            save_mock.assert_not_called()

    def test_ui_state_marks_unknown_sha_pool(self):
        send_as_id = self._prepare_identity(3308, sect_name="阴罗宗")
        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "last_observed_at": 1_779_450_000.0,
                "last_action": "每日献祭",
                "last_result": "success",
                "last_sha_gain": 575,
                "last_extra_sha_gain": 75,
            }
            ui_state = yinluo.get_yinluo_ui_state(now=1_779_450_060.0)

        self.assertFalse(ui_state["observed"]["sha_known"])
        self.assertEqual(0, ui_state["observed"]["sha_current"])
        self.assertEqual(0, ui_state["observed"]["sha_max"])

    def test_ui_state_exposes_yinluo_banner_details_and_blocked_slots(self):
        send_as_id = self._prepare_identity(3309, sect_name="阴罗宗")
        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "last_observed_at": 1_779_450_000.0,
                "banner_name": "灭法幡",
                "banner_rank": "三阶下品",
                "banner_status": "幡息沉潜",
                "battle_bonus_percent": 5,
                "ready_slot_numbers": [3],
                "collect_blocked_ready_slot_numbers": [2, 4],
                "refining_slot_numbers": [6],
                "empty_slot_numbers": [1, 7],
                "exhausted_slot_numbers": [5],
                "soul_lineage": {"凶兽戾魄": 10},
                "banner_traits": {"斗法拘魂": "+10%"},
                "soul_stocks": {"妖兽精魄": 40},
            }
            ui_state = yinluo.get_yinluo_ui_state(now=1_779_450_060.0)

        observed = ui_state["observed"]
        self.assertEqual("灭法幡", observed["banner_name"])
        self.assertEqual("三阶下品", observed["banner_rank"])
        self.assertEqual(5, observed["battle_bonus_percent"])
        self.assertEqual([2, 4], observed["collect_blocked_ready_slot_numbers"])
        self.assertEqual([5], observed["exhausted_slot_numbers"])
        self.assertEqual(10, observed["soul_lineage"]["凶兽戾魄"])
        self.assertEqual("+10%", observed["banner_traits"]["斗法拘魂"])

    def test_set_auto_config_same_value_normalizes_partial_observation(self):
        send_as_id = self._prepare_identity(3305, sect_name="阴罗宗")
        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "last_observed_at": 1_779_450_000.0,
                "auto_config": {
                    "collect": False,
                    "refine": True,
                    "refine_targets": ["凶兽戾魄", "妖兽精魄"],
                },
            }
            with patch.object(yinluo, "save_state") as save_mock:
                ok, message, _ui_state = yinluo.set_yinluo_auto_config({
                    "collect": False,
                    "refine": True,
                    "refine_targets": "凶兽戾魄 妖兽精魄",
                })
            observed = state_module.state["yinluo_observation"]

        self.assertTrue(ok, message)
        save_mock.assert_called_once()
        self.assertIn("auto_next_time", observed)
        self.assertIn("auto_last_error", observed)
        self.assertFalse(observed["auto_config"]["collect"])
        self.assertTrue(observed["auto_config"]["refine"])
        self.assertEqual(["凶兽戾魄", "妖兽精魄"], observed["auto_config"]["refine_targets"])

    def test_set_auto_config_changed_value_saves_plain_payload(self):
        send_as_id = self._prepare_identity(3306, sect_name="阴罗宗")
        with state_module.use_identity(send_as_id):
            state_module.state["yinluo_observation"] = {
                "last_observed_at": 1_779_450_000.0,
                "auto_config": {
                    "collect": True,
                    "refine": True,
                    "blood_forest": True,
                    "demon_summon": True,
                    "convert": False,
                    "convert_amount": 0,
                    "refine_targets": [],
                },
            }
            with patch.object(yinluo, "save_state") as save_mock:
                ok, message, _ui_state = yinluo.set_yinluo_auto_config({
                    "collect": False,
                    "convert": True,
                    "convert_amount": 10000,
                    "refine_targets": "凶兽戾魄 妖兽精魄",
                })
            observed = state_module.state["yinluo_observation"]

        self.assertTrue(ok, message)
        save_mock.assert_called_once()
        self.assertIs(type(observed), dict)
        self.assertIs(type(observed["auto_config"]), dict)
        self.assertIs(type(observed["auto_config"]["refine_targets"]), list)
        self.assertFalse(observed["auto_config"]["collect"])
        self.assertTrue(observed["auto_config"]["convert"])
        self.assertEqual(10000, observed["auto_config"]["convert_amount"])
        self.assertEqual(["凶兽戾魄", "妖兽精魄"], observed["auto_config"]["refine_targets"])

    async def test_ui_updates_yinluo_auto_config_for_available_identity(self):
        from model import ui

        send_as_id = self._prepare_identity(3307, sect_name="阴罗宗")
        with patch.object(yinluo, "save_state"):
            ok, message = await ui.ui_set_yinluo_auto_config(send_as_id, {
                "collect": False,
                "refine": True,
                "blood_forest": False,
                "demon_summon": True,
                "convert": True,
                "convert_amount": 10000,
                "refine_targets": "凶兽戾魄 妖兽精魄",
            })

        self.assertTrue(ok, message)
        with state_module.use_identity(send_as_id):
            config = state_module.state["yinluo_observation"]["auto_config"]
        self.assertFalse(config["collect"])
        self.assertTrue(config["convert"])
        self.assertEqual(10000, config["convert_amount"])
        self.assertEqual(["凶兽戾魄", "妖兽精魄"], config["refine_targets"])


if __name__ == "__main__":
    unittest.main()
