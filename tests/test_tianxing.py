import asyncio
import copy
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import action_guard, runtime
from model import state as state_module
from model import ui
from model.features import deep_retreat, passive_inbox, tianxing
from model.real_message_replay import get_real_message_text, iter_real_message_samples


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "real_message_samples.json"


def real_text(sample_id):
    return get_real_message_text(FIXTURE_PATH, sample_id)


def local_ts(hour, minute=0, *, year=2026, month=6, day=29):
    return time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))


class TianxingParserTests(unittest.TestCase):
    def test_panel_parses_stars_counts_and_calamity(self):
        parsed = tianxing.parse_tianxing_text(
            real_text("tianxing.panel.basic"),
            now=1_780_000_000.0,
            family="tianxing_panel",
        )

        self.assertEqual("天机盘", parsed["action"])
        self.assertEqual("panel", parsed["result"])
        self.assertEqual(["贪狼", "天府", "紫微"], parsed["available_stars"])
        self.assertEqual("panel", parsed["available_stars_source"])
        self.assertEqual("", parsed["fixed_star"])
        self.assertEqual(63, parsed["tianji_value"])
        self.assertEqual(2, parsed["calamity_count"])
        self.assertEqual((189, 6, 40), (parsed["hit_count"], parsed["miss_count"], parsed["change_count"]))

    def test_observe_predict_change_and_modifier_parse(self):
        observe = tianxing.parse_tianxing_text(real_text("tianxing.observe.basic"), now=1_780_000_000.0)
        predict = tianxing.parse_tianxing_text(real_text("tianxing.predict.basic"), now=1_780_000_000.0)
        change = tianxing.parse_tianxing_text(real_text("tianxing.change_fate.basic"), now=1_780_000_000.0)
        modifier = tianxing.parse_tianxing_text(real_text("tianxing.modifier.wild"), now=1_780_000_000.0)
        triggered = tianxing.parse_tianxing_text(real_text("tianxing.modifier.change_triggered"), now=1_780_000_000.0)
        missed = tianxing.parse_tianxing_text(real_text("tianxing.modifier.prediction_miss"), now=1_780_000_000.0)

        self.assertEqual(["天府", "太阴", "贪狼"], observe["available_stars"])
        self.assertEqual("observe", observe["available_stars_source"])
        self.assertEqual("炼制", predict["current_prediction"])
        self.assertGreater(predict["current_prediction_until"], 1_780_000_000.0)
        self.assertEqual("探索", change["current_change"])
        self.assertGreater(change["current_change_until"], 1_780_000_000.0)
        self.assertEqual("prediction_hit", modifier["result"])
        self.assertEqual(1, modifier["last_tianji_gain"])
        self.assertEqual(30, modifier["last_contrib_gain"])
        self.assertIn("太阴", modifier["last_star_effect"])
        self.assertEqual("change_triggered", triggered["result"])
        self.assertNotIn("current_prediction", triggered)
        self.assertNotIn("current_prediction_until", triggered)
        self.assertEqual("", triggered["current_change"])
        self.assertEqual(0, triggered["current_change_until"])
        self.assertEqual("prediction_miss", missed["result"])
        self.assertEqual(1, missed["calamity_delta"])
        self.assertNotIn("current_prediction", missed)
        self.assertNotIn("current_prediction_until", missed)
        self.assertGreater(missed["current_change_until"], 1_780_000_000.0)

    def test_set_star_need_observe_text_clears_panel_stars(self):
        parsed = tianxing.parse_tianxing_text("此命星并未在你今日观命结果中显化，请先 .观命。", now=1_780_000_000.0)

        self.assertEqual("定命", parsed["action"])
        self.assertEqual("need_observe", parsed["result"])
        self.assertEqual([], parsed["available_stars"])
        self.assertEqual("", parsed["available_stars_source"])

    def test_real_help_and_join_texts_are_classified(self):
        help_text = (
            "【天星宗 · 司命推演】\n"
            "天星宗不主强攻，而主预判、改命与消劫。\n"
            "核心命令:\n"
            ".观命 观测今日命盘，刷新本日可选命星。\n"
            ".推命 <闭关|炼制|探索|斗法> 预判接下来 8 小时要做的事。"
        )
        joined_text = (
            "恭喜 @WalterWA2000 道友，你已通过考验，成功拜入【天星宗】，成为本门弟子！\n"
            "宗门已为你铸好一方【司命盘】。先用 .观命 看今日命星，再以 .定命、.推命、.改命 安排你的修行路径。"
        )
        already_text = "你已是【天星宗】的弟子，不可三心二意。"

        help_parsed = tianxing.parse_tianxing_text(help_text, now=1_780_000_000.0)
        joined = tianxing.parse_tianxing_text(joined_text, now=1_780_000_000.0)
        already = tianxing.parse_tianxing_text(already_text, now=1_780_000_000.0)

        self.assertEqual(("玩法帮助", "guide"), (help_parsed["action"], help_parsed["result"]))
        self.assertEqual(("拜入天星宗", "success"), (joined["action"], joined["result"]))
        self.assertEqual(("拜入天星宗", "already_member"), (already["action"], already["result"]))

    def test_panel_without_fixed_star_schedules_prompt_observe(self):
        now = 1_780_000_000.0
        identity_id = 2100
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username="tianxing_parser", label="tianxing_parser", sect_name="天星宗")
            with state_module.use_identity(identity_id):
                state_module.state["tianxing_enabled"] = True
                changed = tianxing.apply_tianxing_passive(real_text("tianxing.panel.basic"), now=now, family="tianxing_panel")
                observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

        self.assertTrue(changed)
        self.assertEqual("panel", observed["available_stars_source"])
        self.assertLessEqual(observed["auto_next_time"], now + 60)

    def test_observe_without_fixed_star_schedules_prompt_strategy(self):
        now = 1_780_000_000.0
        identity_id = 2100
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username="tianxing_parser", label="tianxing_parser", sect_name="天星宗")
            with state_module.use_identity(identity_id):
                state_module.state["tianxing_enabled"] = True
                changed = tianxing.apply_tianxing_passive(real_text("tianxing.observe.basic"), now=now, family="tianxing_observe")
                observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

        self.assertTrue(changed)
        self.assertEqual("observe", observed["available_stars_source"])
        self.assertEqual("", observed["fixed_star"])
        self.assertLessEqual(observed["auto_next_time"], now + 60)

    def test_observe_and_panel_record_current_day_markers(self):
        now = local_ts(0, 1, year=2026, month=6, day=30)
        identity_id = 2100
        panel_text = (
            "【天机盘】\n"
            "今日可选命星: 【紫微】、【贪狼】、【天府】\n"
            "今日已定命星: 未定命\n"
            "当前推命: 无\n"
            "当前改命: 无\n"
            "天机值: 36\n"
            "逆命劫: 0\n"
            "命中 / 落空 / 改命: 42 / 1 / 1"
        )
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username="tianxing_parser", label="tianxing_parser", sect_name="天星宗")
            with state_module.use_identity(identity_id):
                state_module.state["tianxing_enabled"] = True
                tianxing.apply_tianxing_passive(real_text("tianxing.observe.basic"), now=now, family="tianxing_observe")
                observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])
                self.assertEqual(tianxing.get_day_key(now), observed["available_stars_day"])

                tianxing.apply_tianxing_passive(panel_text, now=now + 60, family="tianxing_panel")
                observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

        self.assertEqual(tianxing.get_day_key(now), observed["available_stars_day"])
        self.assertEqual("", observed["fixed_star"])
        self.assertEqual("", observed["fixed_star_day"])

    def test_panel_with_fixed_star_and_no_prediction_wakes_timeline_in_farm_window(self):
        now = 1_780_000_000.0
        identity_id = 2100
        text = (
            "【天机盘】\n"
            "今日已定命星: 【贪狼】\n"
            "当前推命: 无\n"
            "当前改命: 无\n"
            "天机值: 3\n"
            "逆命劫: 0\n"
            "命中 / 落空 / 改命: 3 / 1 / 0"
        )
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username="tianxing_parser", label="tianxing_parser", sect_name="天星宗")
            with state_module.use_identity(identity_id):
                state_module.state["tianxing_enabled"] = True
                state_module.state["tianxing_auto_config"] = {
                    "timeline_enabled": True,
                    "timeline_dry_run_enabled": False,
                    "auto_predict_enabled": True,
                    "craft_farm_enabled": True,
                    "farm_route": "炼制",
                    "farm_window_enabled": True,
                    "farm_window_start": time.strftime("%H:%M", time.localtime(now)),
                    "farm_window_duration_min": 60,
                }
                changed = tianxing.apply_tianxing_passive(text, now=now, family="tianxing_panel")
                observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

        self.assertTrue(changed)
        self.assertEqual("贪狼", observed["fixed_star"])
        self.assertEqual("", observed["current_prediction"])
        self.assertLessEqual(observed["auto_next_time"], now + 60)

    def test_tianxing_retreat_success_parses_prediction_gain_and_cooldown(self):
        now = 1_780_000_000.0
        parsed = tianxing.parse_tianxing_text(
            real_text("tianxing.retreat.success"),
            now=now,
            family="tianxing_retreat_farm",
        )

        self.assertEqual("闭关", parsed["action"])
        self.assertEqual("prediction_hit", parsed["result"])
        self.assertEqual(1, parsed["last_tianji_gain"])
        self.assertEqual(30, parsed["last_contrib_gain"])
        self.assertEqual("", parsed["current_prediction"])
        self.assertEqual(0, parsed["current_prediction_until"])
        self.assertGreater(parsed["normal_retreat_next_time"], now + 10 * 60)

    def test_craft_farm_parses_tianxing_hit_and_clears_prediction(self):
        now = 1_780_000_000.0
        parsed = tianxing.parse_tianxing_text(
            "炼制结束！\n"
            "共开炉 1 次，成功 1 次。\n"
            "最终获得【玄铁剑】x1！\n\n"
            "命盘【天府】照命，主丹器之成与稳守之势，炼制更稳，斗法更耐打，偶有额外成品。\n"
            "【推命命中】司命演算吻合，天机值 +1，宗门贡献 +30",
            now=now,
            family="tianxing_craft_farm",
        )

        self.assertEqual("炼制", parsed["action"])
        self.assertEqual("prediction_hit", parsed["result"])
        self.assertEqual("玄铁剑", parsed["craft_item"])
        self.assertEqual(1, parsed["craft_success_count"])
        self.assertEqual(1, parsed["last_tianji_gain"])
        self.assertEqual("", parsed["current_prediction"])
        self.assertEqual(0, parsed["current_prediction_until"])

    def test_command_reply_minor_wording_variants_still_parse(self):
        now = 1_780_000_000.0
        predict = tianxing.parse_tianxing_text(
            "你为【闭关】推下了一段命数，司命盘微微转动。\n这道推命将在 8小时 内等待应验。",
            now=now,
            family="tianxing_predict",
        )
        change = tianxing.parse_tianxing_text(
            "你为【探索】预留一次改命回天。若遇大厄，司命盘会替你扳回一线。\n改命将在 12小时 内待发。",
            now=now,
            family="tianxing_change_fate",
        )

        self.assertEqual("推命", predict["action"])
        self.assertEqual("闭关", predict["current_prediction"])
        self.assertGreater(predict["current_prediction_until"], now)
        self.assertEqual("改命", change["action"])
        self.assertEqual("探索", change["current_change"])
        self.assertGreater(change["current_change_until"], now)

    def test_existing_prediction_and_change_cooldown_real_text_calibrate_state(self):
        now = 1_780_000_000.0
        predict = tianxing.parse_tianxing_text(
            "你已有一道关于 【炼制】 的推命尚未应验，还需等待 4小时58分钟。",
            now=now,
            family="tianxing_predict",
        )
        change = tianxing.parse_tianxing_text(
            "你已有一道关于 【探索】 的改命尚未耗尽，还可维持 21小时57分钟。",
            now=now,
            family="tianxing_change_fate",
        )

        self.assertEqual("推命", predict["action"])
        self.assertEqual("cooldown", predict["result"])
        self.assertEqual("炼制", predict["current_prediction"])
        self.assertGreater(predict["current_prediction_until"], now + 4 * 3600)
        self.assertEqual("改命", change["action"])
        self.assertEqual("cooldown", change["result"])
        self.assertEqual("探索", change["current_change"])
        self.assertGreater(change["current_change_until"], now + 21 * 3600)

    def test_clear_calamity_noop_real_text_calibrates_zero(self):
        parsed = tianxing.parse_tianxing_text(
            "你当前并无逆命劫缠身，无需消劫。",
            now=1_780_000_000.0,
            family="tianxing_clear_calamity",
        )

        self.assertEqual("消劫", parsed["action"])
        self.assertEqual("noop", parsed["result"])
        self.assertEqual(0, parsed["calamity_count"])

    def test_retreat_farm_cooldown_and_heqi_real_text_parse(self):
        now = 1_780_000_000.0
        cooldown = tianxing.parse_tianxing_text(
            "灵气尚未平复，无法立即再次闭关。请在11分钟31秒后再试。",
            now=now,
            family="tianxing_retreat_farm",
        )
        heqi = tianxing.parse_tianxing_text(
            real_text("tianxing.retreat_farm.heqi_dan_success"),
            now=now,
            family="tianxing_retreat_farm",
        )

        self.assertEqual("闭关", cooldown["action"])
        self.assertEqual("cooldown", cooldown["result"])
        self.assertGreater(cooldown["normal_retreat_next_time"], now + 10 * 60)
        self.assertEqual("合气丹", heqi["action"])
        self.assertEqual("success", heqi["result"])
        self.assertEqual(now, heqi["normal_retreat_next_time"])

    def test_retreat_farm_heqi_exchange_and_donation_text_parse(self):
        now = 1_780_000_000.0
        missing = tianxing.parse_tianxing_text(
            "你的储物袋中没有名为【合气丹】的可用物品。",
            now=now,
            family="tianxing_retreat_farm",
        )
        exchanged = tianxing.parse_tianxing_text(
            "兑换成功！\n你消耗了 1500 点贡献，获得了【合气丹】x10，已放入你的储物袋。",
            now=now,
            family="tianxing_retreat_farm",
        )
        shortage = tianxing.parse_tianxing_text(
            "你的宗门贡献不足！\n兑换【合气丹】x10 需要 1500 点贡献，你只有 270 点。",
            now=now,
            family="tianxing_retreat_farm",
        )
        donated = tianxing.parse_tianxing_text(
            "你向宗门捐献了 【灵石】x200，获得了 1400 点宗门贡献！",
            now=now,
            family="tianxing_retreat_farm",
        )

        self.assertEqual(("合气丹", "missing"), (missing["action"], missing["result"]))
        self.assertEqual(("兑换合气丹", "success", 10), (exchanged["action"], exchanged["result"], exchanged["exchange_count"]))
        self.assertEqual(("兑换合气丹", "contribution_shortage", 1500, 270), (shortage["action"], shortage["result"], shortage["contribution_need"], shortage["contribution_have"]))
        self.assertEqual(("宗门捐献", "success", 200, 1400), (donated["action"], donated["result"], donated["donate_count"], donated["contribution_gain"]))

    def test_retreat_farm_parameterized_commands_route_to_family_and_guard(self):
        self.assertEqual("tianxing_retreat_farm", runtime.resolve_reply_family(".兑换 合气丹*10"))
        self.assertEqual("tianxing_retreat_farm", runtime.resolve_reply_family(".宗门捐献 灵石*200"))
        self.assertEqual("tianxing_heqi_exchange", action_guard.resolve_action_key(".兑换 合气丹*10"))
        self.assertEqual("tianxing_lingshi_donation", action_guard.resolve_action_key(".宗门捐献 灵石*200"))

    def test_unknown_tianxing_wording_is_observed_without_state_claim(self):
        parsed = tianxing.parse_tianxing_text(
            "司命盘忽然震颤，似有星辉流转，但没有出现明确的推命或改命结果。",
            now=1_780_000_000.0,
            family="tianxing_panel",
        )

        self.assertEqual("未知天星宗文案", parsed["action"])
        self.assertEqual("observed", parsed["result"])
        self.assertNotIn("current_prediction", parsed)
        self.assertNotIn("current_change", parsed)

    def test_observe_parses_combined_sect_info_and_inline_star(self):
        parsed = tianxing.parse_tianxing_text(
            "你所属的宗门: 【天星宗】\n\n"
            "掌门: 玄宁生 (@hfsscxf)\n"
            "司命盘要诀:\n"
            ".观命 先看今日可选命星，再用 .定命 <命星> 锁定今日主修路线；.推命 <闭关|炼制|探索|斗法> 押你接下来要走的路，命中可积攒天机值；.改命 <闭关|炼制|探索|斗法> 把天机值换成一次后手；.天机盘 查看当前推命、改命与逆命劫。  观命结果】\n"
            "你引动司命盘，今日可定下的命星如下：\n"
            "【太阴】 - 主趋吉避凶，探索更易避祸，斗法更善脱身，但闭关悟性略降。\n"
            "【贪狼】 - 主偏财夺势，闭关奇遇与探寻收获更盛，斗法更擅夺取战果，但炼制时心火浮躁。\n"
            "【天府】 - 主丹器之成与稳守之势，炼制更稳，斗法更耐打，偶有额外成品。\n"
            "请使用 .定命 <命星> 锁定今日命轨。【紫微】 - 主悟道与先机，闭关更稳更厚，斗法更易抢先，但炼制时心念易散。",
            now=1_780_000_000.0,
        )

        self.assertEqual("观命", parsed["action"])
        self.assertEqual("success", parsed["result"])
        self.assertEqual(["太阴", "贪狼", "天府", "紫微"], parsed["available_stars"])

    def test_clear_calamity_and_join_blocked_parse(self):
        clear = tianxing.parse_tianxing_text(real_text("tianxing.clear_calamity.basic"), now=1_780_000_000.0)
        blocked = tianxing.parse_tianxing_text(real_text("tianxing.join.not_qualified"), now=1_780_000_000.0)
        not_member = tianxing.parse_tianxing_text(
            "你并非天星宗弟子，司命盘不会为你显化命轨。",
            now=1_780_000_000.0,
        )

        self.assertEqual("消劫", clear["action"])
        self.assertEqual("success", clear["result"])
        self.assertEqual("not_qualified", blocked["result"])
        self.assertEqual("无法感应九天星辰之力", blocked["last_error"])
        self.assertEqual(("天星身份", "not_member"), (not_member["action"], not_member["result"]))
        self.assertEqual("非天星宗弟子，司命盘不会显化命轨", not_member["last_error"])

    def test_real_retreat_success_parses_tianxing_bonus(self):
        parsed = tianxing.parse_tianxing_text(
            real_text("tianxing.retreat.success"),
            now=1_781_505_850.0,
            family="tianxing_retreat",
        )

        self.assertEqual("闭关", parsed["action"])
        self.assertEqual("prediction_hit", parsed["result"])
        self.assertEqual(239, parsed["last_bonus_gain"])
        self.assertEqual(1, parsed["last_tianji_gain"])


class TianxingManualPlanTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        self.identity_id = 2101
        state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(
            self.identity_id,
            username="tianxing_manual",
            label="tianxing_manual",
            sect_name="天星宗",
        )

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_panel_and_observe_are_manual_queries_without_prior_observation(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True

            panel = tianxing.build_tianxing_manual_plan("panel", now=now)
            observe = tianxing.build_tianxing_manual_plan("observe", now=now)

        self.assertTrue(panel["allowed"])
        self.assertEqual(".天机盘", panel["command"])
        self.assertEqual("tianxing_panel", panel["family"])
        self.assertTrue(observe["allowed"])
        self.assertEqual(".观命", observe["command"])
        self.assertEqual("tianxing_observe", observe["family"])

    def test_set_star_requires_recent_available_star_and_allows_switching_fixed_star(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["天府", "太阴"],
                "fixed_star": "",
            }
            allowed = tianxing.build_tianxing_manual_plan("set_star", "天府", now=now)
            unavailable = tianxing.build_tianxing_manual_plan("set_star", "紫微", now=now)

            state_module.state["tianxing_observation"]["fixed_star"] = "太阴"
            switched = tianxing.build_tianxing_manual_plan("set_star", "天府", now=now)
            state_module.state["tianxing_observation"]["fixed_star"] = "天府"
            same = tianxing.build_tianxing_manual_plan("set_star", "天府", now=now)

        self.assertTrue(allowed["allowed"])
        self.assertEqual(".定命 天府", allowed["command"])
        self.assertFalse(unavailable["allowed"])
        self.assertIn("今日可选命星", unavailable["reason"])
        self.assertTrue(switched["allowed"])
        self.assertEqual(".定命 天府", switched["command"])
        self.assertFalse(same["allowed"])
        self.assertIn("当前已是目标命星", same["reason"])

    def test_set_star_allows_same_star_again_on_next_day(self):
        now = local_ts(0, 2, year=2026, month=6, day=30)
        yesterday = local_ts(23, 50, year=2026, month=6, day=29)
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴", "贪狼"],
                "available_stars_day": tianxing.get_day_key(now),
                "fixed_star": "太阴",
                "fixed_star_day": tianxing.get_day_key(yesterday),
            }
            plan = tianxing.build_tianxing_manual_plan("set_star", "太阴", now=now)

        self.assertTrue(plan["allowed"])
        self.assertEqual(".定命 太阴", plan["command"])

    def test_predict_change_fate_and_clear_calamity_use_observed_cooldowns_and_resources(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 3,
                "calamity_count": 1,
            }
            predict = tianxing.build_tianxing_manual_plan("predict", "炼制", now=now)
            change = tianxing.build_tianxing_manual_plan("change_fate", "探索", now=now)
            clear = tianxing.build_tianxing_manual_plan("clear_calamity", now=now)

            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 600
            prediction_cooldown = tianxing.build_tianxing_manual_plan("predict", "炼制", now=now)

            state_module.state["tianxing_observation"]["current_prediction_until"] = 0
            state_module.state["tianxing_observation"]["current_prediction"] = "炼制"
            prediction_unknown_time = tianxing.build_tianxing_manual_plan("predict", "炼制", now=now)

            state_module.state["tianxing_observation"]["current_prediction"] = ""
            state_module.state["tianxing_observation"]["current_change_until"] = now + 600
            change_cooldown = tianxing.build_tianxing_manual_plan("change_fate", "探索", now=now)

            state_module.state["tianxing_observation"]["current_change_until"] = 0
            state_module.state["tianxing_observation"]["current_change"] = "探索"
            change_unknown_time = tianxing.build_tianxing_manual_plan("change_fate", "探索", now=now)

            state_module.state["tianxing_observation"]["current_change"] = ""
            state_module.state["tianxing_observation"]["tianji_value"] = 2
            tianji_shortage = tianxing.build_tianxing_manual_plan("change_fate", "探索", now=now)

            state_module.state["tianxing_observation"]["tianji_value"] = 3
            state_module.state["tianxing_observation"]["calamity_count"] = 0
            no_calamity = tianxing.build_tianxing_manual_plan("clear_calamity", now=now)

        self.assertTrue(predict["allowed"])
        self.assertEqual(".推命 炼制", predict["command"])
        self.assertTrue(change["allowed"])
        self.assertEqual(".改命 探索", change["command"])
        self.assertTrue(clear["allowed"])
        self.assertEqual(".消劫", clear["command"])
        self.assertFalse(prediction_cooldown["allowed"])
        self.assertIn("已有推命", prediction_cooldown["reason"])
        self.assertFalse(prediction_unknown_time["allowed"])
        self.assertIn("时间不可解析", prediction_unknown_time["reason"])
        self.assertFalse(change_cooldown["allowed"])
        self.assertIn("已有改命", change_cooldown["reason"])
        self.assertFalse(change_unknown_time["allowed"])
        self.assertIn("时间不可解析", change_unknown_time["reason"])
        self.assertFalse(tianji_shortage["allowed"])
        self.assertIn("天机值不足", tianji_shortage["reason"])
        self.assertFalse(no_calamity["allowed"])
        self.assertIn("未记录逆命劫", no_calamity["reason"])

    def test_ui_config_save_preserves_hidden_fields_and_derives_farm_route(self):
        async def run_save():
            with state_module.use_identity(self.identity_id):
                state_module.state["tianxing_auto_config"] = {
                    "star_priority": ["太阴"],
                    "route_priority": ["斗法"],
                    "change_route_priority": ["探索", "闭关"],
                    "farm_route": "闭关",
                    "craft_farm_enabled": False,
                }
            with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
                return await ui.ui_set_tianxing_config(
                    self.identity_id,
                    {
                        "auto_set_star_enabled": True,
                        "craft_farm_enabled": True,
                    },
                )

        ok, message = asyncio.run(run_save())
        with state_module.use_identity(self.identity_id):
            config = tianxing.normalize_tianxing_auto_config(state_module.state.get("tianxing_auto_config"))

        self.assertTrue(ok, message)
        self.assertEqual(["太阴"], config["star_priority"])
        self.assertEqual(["斗法"], config["route_priority"])
        self.assertEqual(["探索"], config["change_route_priority"])
        self.assertEqual("炼制", config["farm_route"])
        self.assertTrue(config["craft_farm_enabled"])
        self.assertTrue(config["auto_set_star_enabled"])

    def test_status_hides_stale_observation_when_module_disabled(self):
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = False
            state_module.state["tianxing_observation"] = {
                "last_observed_at": 1_780_000_000.0,
                "last_action": "推命",
                "last_error": "旧天星错误",
            }

            text = tianxing.get_tianxing_status_text()

        self.assertIn("模块：关闭", text)
        self.assertIn("不展示旧观察记录", text)
        self.assertNotIn("旧天星错误", text)
        self.assertNotIn("推命", text)

    def test_state_actions_block_dirty_time_fields_without_guessing(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["天府"],
                "fixed_star": "",
                "current_prediction_until": "nan",
                "current_change_until": 0,
                "tianji_value": 3,
                "calamity_count": 1,
            }

            plan = tianxing.build_tianxing_manual_plan("predict", "炼制", now=now)

        self.assertFalse(plan["allowed"])
        self.assertIn("状态字段异常", plan["reason"])

    def test_status_text_tolerates_dirty_time_fields(self):
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": "观测时间异常",
                "current_prediction_until": "inf",
                "current_change_until": "-inf",
                "auto_next_time": "nan",
                "recent": [{"ts": "inf", "action": "推命", "result": "success"}],
            }

            text = tianxing.get_tianxing_status_text()

        self.assertIn("🌌 天星宗", text)
        self.assertIn("状态异常", text)
        self.assertIn("未设置", text)

    def test_route_preflight_requests_timeline_when_enabled_and_status_missing(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {}

            plan = tianxing.build_tianxing_route_preflight_plan(
                "探索",
                reason="野外历练",
                now=now,
                config={"timeline_enabled": True},
            )

        self.assertEqual("timeline_waiting", plan["stage"])
        self.assertFalse(plan["route_allowed"])
        self.assertEqual("", plan["prepare_command"])
        self.assertTrue(plan["timeline_required"])
        self.assertTrue(plan["lab_only"])

    def test_route_preflight_does_not_insert_strategy_without_timeline(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "fixed_star": "太阴",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 42,
                "calamity_count": 0,
            }
            change_plan = tianxing.build_tianxing_route_preflight_plan(
                "探索",
                reason="探寻裂缝",
                now=now,
                config={
                    "auto_change_fate_enabled": True,
                    "auto_predict_enabled": True,
                    "strategy_dry_run_enabled": True,
                    "min_tianji_for_change": 6,
                },
            )

            state_module.state["tianxing_observation"]["tianji_value"] = 2
            ready_plan = tianxing.build_tianxing_route_preflight_plan(
                "探索",
                reason="野外历练",
                now=now,
                config={
                    "auto_change_fate_enabled": True,
                    "auto_predict_enabled": True,
                    "strategy_dry_run_enabled": True,
                    "min_tianji_for_change": 6,
                },
            )

        self.assertEqual("timeline_disabled", change_plan["stage"])
        self.assertEqual("", change_plan["prepare_command"])
        self.assertTrue(change_plan["route_allowed"])
        self.assertEqual("timeline_disabled", ready_plan["stage"])
        self.assertEqual("", ready_plan["prepare_command"])
        self.assertTrue(ready_plan["route_allowed"])

    def test_route_preflight_requires_and_consumes_timeline_release_when_enabled(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "fixed_star": "太阴",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "探索",
                "current_change_until": now + 3600,
                "tianji_value": 42,
            }
            waiting = tianxing.build_tianxing_route_preflight_plan(
                "探索",
                reason="探寻裂缝",
                now=now,
                config={"timeline_enabled": True},
            )
            state_module.state["tianxing_timeline_state"] = {
                "released_routes": {
                    "探索": {"released_at": now - 5, "plan_id": "test", "reason": "confirmed"},
                },
            }
            released = tianxing.build_tianxing_route_preflight_plan(
                "探索",
                reason="探寻裂缝",
                now=now,
                config={"timeline_enabled": True},
            )

        self.assertEqual("timeline_waiting", waiting["stage"])
        self.assertFalse(waiting["route_allowed"])
        self.assertTrue(waiting["timeline_required"])
        self.assertEqual("timeline_released", released["stage"])
        self.assertTrue(released["route_allowed"])

    def test_route_preflight_can_require_change_fate_release(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "current_prediction": "探索",
                "current_prediction_until": now + 3600,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 42,
            }
            state_module.state["tianxing_timeline_state"] = {
                "released_routes": {
                    "探索": {"released_at": now - 5, "plan_id": "test", "reason": "prediction confirmed", "basis": "prediction"},
                },
            }
            normal = tianxing.build_tianxing_route_preflight_plan(
                "探索",
                reason="野外历练",
                now=now,
                config={"timeline_enabled": True},
            )
            strict = tianxing.build_tianxing_route_preflight_plan(
                "探索",
                reason="野外历练",
                now=now,
                config={"timeline_enabled": True},
                require_change_fate=True,
            )

            state_module.state["tianxing_observation"]["current_change"] = "探索"
            state_module.state["tianxing_observation"]["current_change_until"] = now + 3600
            state_module.state["tianxing_timeline_state"]["released_routes"]["探索"]["basis"] = "change_fate"
            strict_released = tianxing.build_tianxing_route_preflight_plan(
                "探索",
                reason="野外历练",
                now=now,
                config={"timeline_enabled": True},
                require_change_fate=True,
            )

        self.assertEqual("timeline_released", normal["stage"])
        self.assertTrue(normal["route_allowed"])
        self.assertEqual("timeline_waiting_change_fate", strict["stage"])
        self.assertFalse(strict["route_allowed"])
        self.assertTrue(strict["timeline_required"])
        self.assertEqual("change_fate_active", strict_released["stage"])
        self.assertTrue(strict_released["route_allowed"])

    def test_route_preflight_requires_new_prediction_after_prediction_was_consumed(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "current_prediction": "探索",
                "current_prediction_until": now + 3600,
                "current_prediction_set_at": now - 600,
                "prediction_consumed_route": "探索",
                "prediction_consumed_at": now - 10,
                "current_change": "探索",
                "current_change_until": now + 12 * 3600,
                "tianji_value": 42,
            }
            state_module.state["tianxing_timeline_state"] = {
                "released_routes": {
                    "探索": {"released_at": now - 20, "plan_id": "old", "reason": "old release", "basis": "change_fate"},
                },
            }
            plan = tianxing.build_tianxing_route_preflight_plan(
                "探索",
                reason="探寻裂缝",
                now=now,
                config={"timeline_enabled": True},
                require_change_fate=True,
            )

        self.assertEqual("timeline_waiting_change_fate", plan["stage"])
        self.assertFalse(plan["route_allowed"])
        self.assertTrue(plan["timeline_required"])

    def test_tianxing_result_consumes_prediction_and_downstream_release(self):
        now = 1_780_000_000.0
        text = (
            "【野外历练 · 妖兽遭遇】\n"
            "命盘【贪狼】照命，主偏财夺势。\n"
            "【推命命中】司命演算吻合，天机值 +1，宗门贡献 +30\n"
            "【改命待发】此道改命尚可维持 23小时49分钟\n"
            "@WalterWA2000 遭遇 变异荒古鳞兽。\n"
            "一番斗法后，妖兽伏诛。"
        )
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "current_prediction": "探索",
                "current_prediction_until": now + 3600,
                "current_prediction_set_at": now - 600,
                "current_change": "探索",
                "current_change_until": now + 12 * 3600,
                "tianji_value": 42,
            }
            state_module.state["tianxing_timeline_state"] = {
                "phase": "downstream_released",
                "active_step": {"action": "release_downstream", "route": "探索", "status": "released", "release_basis": "change_fate"},
                "released_routes": {
                    "探索": {"released_at": now - 20, "plan_id": "old", "reason": "old release", "basis": "change_fate"},
                },
            }

            self.assertTrue(tianxing.apply_tianxing_passive(text, now=now))
            observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertEqual("探索", observed["prediction_consumed_route"])
        self.assertEqual(now, observed["prediction_consumed_at"])
        self.assertEqual("", observed["current_prediction"])
        self.assertNotIn("探索", timeline["released_routes"])
        self.assertEqual("blocked_replan", timeline["phase"])

    def test_route_preflight_does_not_block_non_tianxing_identity(self):
        now = 1_780_000_000.0
        state_module.update_send_as_profile(self.identity_id, sect_name="散修")
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True

            plan = tianxing.build_tianxing_route_preflight_plan("探索", now=now)

        self.assertEqual("unavailable", plan["stage"])
        self.assertTrue(plan["route_allowed"])
        self.assertEqual("", plan["prepare_command"])

    def test_route_preflight_blocks_conflicting_prediction_to_avoid_calamity(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴"],
                "fixed_star": "太阴",
                "current_prediction": "闭关",
                "current_prediction_until": now + 1800,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_route_preflight_plan("探索", reason="探寻裂缝", now=now)

        self.assertEqual("prediction_conflict", plan["stage"])
        self.assertFalse(plan["route_allowed"])
        self.assertEqual("", plan["prepare_command"])
        self.assertEqual(now + 1800, plan["blocked_until"])
        self.assertIn("避免逆命", plan["reason"])

    def test_route_preflight_blocks_active_change_fate_when_prediction_conflicts(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "current_prediction": "炼制",
                "current_prediction_until": now + 8 * 3600,
                "current_change": "探索",
                "current_change_until": now + 12 * 3600,
                "tianji_value": 32,
            }
            plan = tianxing.build_tianxing_route_preflight_plan(
                "探索",
                reason="野外历练",
                now=now,
                config={"timeline_enabled": True},
                require_change_fate=True,
            )

        self.assertEqual("prediction_conflict", plan["stage"])
        self.assertFalse(plan["route_allowed"])
        self.assertIn("已有 炼制 推命", plan["reason"])

    def test_route_preflight_blocks_conflicting_prediction_even_when_override_configured(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼", "天府"],
                "fixed_star": "贪狼",
                "current_prediction": "探索",
                "current_prediction_until": now + 1800,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 24,
            }
            plan = tianxing.build_tianxing_route_preflight_plan(
                "炼制",
                reason="天星炼制攒点发送前复核",
                now=now,
                config={"timeline_enabled": True, "allow_prediction_override_enabled": True},
            )

        self.assertEqual("prediction_conflict", plan["stage"])
        self.assertFalse(plan["route_allowed"])
        self.assertEqual("", plan["prepare_command"])
        self.assertEqual(now + 1800, plan["blocked_until"])
        self.assertIn("已有 探索 推命", plan["reason"])

    def test_manual_pause_blocks_route_preflight_for_tianxing_identity(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴"],
                "fixed_star": "太阴",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
                "automation_paused_until": -1,
                "automation_paused_reason": "手动测试",
            }
            plan = tianxing.build_tianxing_route_preflight_plan(
                "探索",
                reason="野外历练",
                now=now,
                config={"timeline_enabled": True},
            )

        self.assertEqual("automation_paused", plan["stage"])
        self.assertFalse(plan["route_allowed"])
        self.assertEqual("", plan["prepare_command"])
        self.assertIn("已暂停", plan["reason"])
        self.assertIn("避免逆命", plan["reason"])

    def test_timeline_plan_marks_farm_route_for_predict_and_consume_route_for_change(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "fixed_star": "贪狼",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "闭关", "kind": "farm", "start_at": now + 60, "end_at": now + 3600, "weight": 8, "reason": "闭关修炼窗口"},
                    {"route": "探索", "kind": "consume", "start_at": now + 120, "end_at": now + 240, "weight": 2, "reason": "探寻裂缝"},
                    {"route": "探索", "kind": "consume", "start_at": now + 900, "end_at": now + 960, "weight": 2, "reason": "野外历练"},
                ],
                config={
                    "auto_predict_enabled": True,
                    "auto_change_fate_enabled": True,
                    "min_tianji_for_change": 6,
                },
            )

        self.assertTrue(plan["lab_only"])
        self.assertEqual("闭关", plan["dominant_route"])
        self.assertEqual("need_predict", plan["stage"])
        self.assertTrue(plan["should_predict"])
        self.assertEqual("闭关", plan["predict_route"])
        self.assertEqual("探索", plan["recommended_change_route"])
        self.assertEqual(
            [("predict", "闭关"), ("change_fate", "探索"), ("release_downstream", "探索")],
            [(step["action"], step["arg"]) for step in plan["steps"]],
        )

    def test_timeline_plan_never_auto_changes_fate_for_retreat(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "fixed_star": "紫微",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 42,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[{
                    "route": "闭关",
                    "kind": "consume",
                    "start_at": now,
                    "end_at": now + 60,
                    "weight": 10,
                    "reason": "普通闭关攒点",
                    "require_change_fate": True,
                }],
                config={
                    "auto_change_fate_enabled": True,
                    "min_tianji_for_change": 3,
                    "timeline_enabled": True,
                },
            )

        self.assertEqual("auto_change_fate_route_forbidden", plan["stage"])
        self.assertEqual("", plan["recommended_change_route"])
        self.assertEqual("", plan["release_route"])
        self.assertNotIn("change_fate", [step["action"] for step in plan["steps"]])
        self.assertIn("自动改命仅允许", plan["change_reason"])

    def test_timeline_plan_reuses_existing_explore_change_fate_without_resending(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "fixed_star": "贪狼",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "探索",
                "current_change_until": now + 12 * 3600,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "探索", "kind": "consume", "start_at": now, "end_at": now + 60, "weight": 10, "reason": "野外历练"},
                ],
                config={
                    "auto_predict_enabled": True,
                    "auto_change_fate_enabled": True,
                    "min_tianji_for_change": 3,
                },
            )

        self.assertEqual("探索", plan["recommended_change_route"])
        self.assertEqual("探索", plan["release_route"])
        self.assertEqual([("predict", "探索"), ("release_downstream", "探索")], [(step["action"], step["arg"]) for step in plan["steps"]])
        self.assertNotIn("change_fate", [step["action"] for step in plan["steps"]])
        self.assertIn("改命已待发", plan["change_reason"])

    def test_timeline_plan_marks_other_change_fate_as_conflict_for_required_explore(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "fixed_star": "贪狼",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "闭关",
                "current_change_until": now + 12 * 3600,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[{
                    "route": "探索",
                    "kind": "consume",
                    "start_at": now,
                    "end_at": now + 60,
                    "weight": 10,
                    "reason": "探寻裂缝",
                    "require_change_fate": True,
                }],
                config={
                    "auto_predict_enabled": True,
                    "auto_change_fate_enabled": True,
                    "min_tianji_for_change": 3,
                },
            )

        self.assertEqual("change_fate_conflict", plan["stage"])
        self.assertEqual("", plan["release_route"])
        self.assertEqual([("predict", "探索")], [(step["action"], step["arg"]) for step in plan["steps"]])
        self.assertIn("已有 闭关 改命", plan["predict_reason"])

    def test_timeline_plan_observes_before_set_star_when_stars_only_from_panel(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["太阴", "贪狼", "天府"],
                "available_stars_source": "panel",
                "fixed_star": "",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 3,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "闭关", "kind": "farm", "start_at": now + 60, "end_at": now + 3600, "weight": 8, "reason": "闭关修炼窗口"},
                ],
                config={
                    "auto_observe_enabled": True,
                    "auto_set_star_enabled": True,
                    "auto_predict_enabled": True,
                    "timeline_enabled": True,
                },
            )

        self.assertEqual("need_set_star", plan["stage"])
        self.assertEqual([("observe", "")], [(step["action"], step["arg"]) for step in plan["steps"]])
        self.assertEqual(".观命", plan["steps"][0]["command"])

    def test_timeline_plan_keeps_taiyin_default_before_explore(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼", "太阴", "天府"],
                "available_stars_source": "observe",
                "fixed_star": "太阴",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "探索", "kind": "consume", "start_at": now + 60, "end_at": now + 240, "weight": 10, "reason": "野外历练"},
                ],
                config={
                    "auto_set_star_enabled": True,
                    "auto_change_fate_enabled": True,
                    "min_tianji_for_change": 3,
                    "timeline_enabled": True,
                },
            )

        self.assertEqual("observe_only", plan["stage"])
        self.assertNotIn("set_star", [step["action"] for step in plan["steps"]])
        self.assertEqual(
            [("change_fate", "探索"), ("release_downstream", "探索")],
            [(step["action"], step["arg"]) for step in plan["steps"]],
        )

    def test_timeline_plan_can_switch_to_tanlang_when_special_star_enabled(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼", "太阴", "天府"],
                "available_stars_source": "observe",
                "fixed_star": "太阴",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "探索", "kind": "consume", "start_at": now + 60, "end_at": now + 240, "weight": 10, "reason": "野外历练"},
                ],
                config={
                    "auto_set_star_enabled": True,
                    "auto_change_fate_enabled": True,
                    "route_special_star_enabled": True,
                    "min_tianji_for_change": 3,
                    "timeline_enabled": True,
                },
            )

        self.assertEqual("need_set_star", plan["stage"])
        self.assertEqual(("set_star", "贪狼", "探索"), (plan["steps"][0]["action"], plan["steps"][0]["arg"], plan["steps"][0]["route"]))
        self.assertIn(("change_fate", "探索"), [(step["action"], step["arg"]) for step in plan["steps"]])

    def test_timeline_plan_prioritizes_change_fate_over_tanlang_for_critical_explore(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼", "太阴", "天府"],
                "available_stars_source": "observe",
                "fixed_star": "太阴",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "探索", "kind": "consume", "start_at": now + 60, "end_at": now + 240, "weight": 10, "reason": "探寻裂缝", "require_change_fate": True},
                ],
                config={
                    "auto_set_star_enabled": True,
                    "auto_change_fate_enabled": True,
                    "min_tianji_for_change": 3,
                    "timeline_enabled": True,
                },
            )

        self.assertEqual("observe_only", plan["stage"])
        self.assertEqual(
            [("change_fate", "探索"), ("release_downstream", "探索")],
            [(step["action"], step["arg"]) for step in plan["steps"]],
        )
        self.assertNotIn("set_star", [step["action"] for step in plan["steps"]])

    def test_timeline_plan_does_not_switch_to_ziwei_before_retreat_by_default(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼", "紫微", "天府"],
                "available_stars_source": "observe",
                "fixed_star": "贪狼",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "闭关", "kind": "farm", "start_at": now + 60, "end_at": now + 3600, "weight": 8, "reason": "闭关/出关窗口"},
                ],
                config={
                    "auto_set_star_enabled": True,
                    "auto_predict_enabled": True,
                    "timeline_enabled": True,
                },
            )

        self.assertEqual("need_predict", plan["stage"])
        self.assertNotIn("set_star", [step["action"] for step in plan["steps"]])
        self.assertEqual(("predict", "闭关"), (plan["steps"][0]["action"], plan["steps"][0]["arg"]))

    def test_timeline_plan_can_switch_to_ziwei_before_retreat_when_special_star_enabled(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼", "紫微", "天府"],
                "available_stars_source": "observe",
                "fixed_star": "贪狼",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "闭关", "kind": "farm", "start_at": now + 60, "end_at": now + 3600, "weight": 8, "reason": "闭关/出关窗口"},
                ],
                config={
                    "auto_set_star_enabled": True,
                    "auto_predict_enabled": True,
                    "route_special_star_enabled": True,
                    "timeline_enabled": True,
                },
            )

        self.assertEqual("need_set_star", plan["stage"])
        self.assertEqual([("set_star", "紫微")], [(step["action"], step["arg"]) for step in plan["steps"]])

    def test_timeline_plan_uses_next_window_for_star_before_later_consume(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼", "紫微", "太阴"],
                "available_stars_source": "observe",
                "fixed_star": "贪狼",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "闭关", "kind": "farm", "start_at": now + 60, "end_at": now + 3600, "weight": 8, "reason": "闭关/出关窗口"},
                    {"route": "探索", "kind": "consume", "start_at": now + 600, "end_at": now + 900, "weight": 10, "reason": "野外历练", "require_change_fate": True},
                ],
                config={
                    "auto_set_star_enabled": True,
                    "auto_change_fate_enabled": True,
                    "min_tianji_for_change": 3,
                    "timeline_enabled": True,
                },
            )

        self.assertEqual("observe_only", plan["stage"])
        self.assertNotIn("set_star", [step["action"] for step in plan["steps"]])

    def test_timeline_plan_still_predicts_explore_when_change_fate_conflicts(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼", "紫微", "太阴"],
                "available_stars_source": "observe",
                "fixed_star": "贪狼",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "闭关",
                "current_change_until": now + 3600,
                "tianji_value": 42,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "探索", "kind": "consume", "start_at": now, "end_at": now + 60, "weight": 10, "reason": "野外历练", "require_change_fate": True},
                ],
                config={
                    "auto_predict_enabled": True,
                    "auto_change_fate_enabled": True,
                    "min_tianji_for_change": 6,
                    "timeline_enabled": True,
                },
            )

        self.assertEqual("change_fate_conflict", plan["stage"])
        self.assertEqual([("predict", "探索")], [(step["action"], step["arg"]) for step in plan["steps"]])
        self.assertEqual("", plan["release_route"])

    def test_timeline_plan_can_switch_to_tianfu_before_craft_when_special_star_enabled(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["紫微", "天府", "太阴"],
                "available_stars_source": "observe",
                "fixed_star": "紫微",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "炼制", "kind": "farm", "start_at": now + 60, "end_at": now + 3600, "weight": 8, "reason": "炼制攒点"},
                ],
                config={
                    "auto_set_star_enabled": True,
                    "auto_predict_enabled": True,
                    "route_special_star_enabled": True,
                    "timeline_enabled": True,
                },
            )

        self.assertEqual("need_set_star", plan["stage"])
        self.assertEqual(("set_star", "天府"), (plan["steps"][0]["action"], plan["steps"][0]["arg"]))
        self.assertNotIn("predict", [step["action"] for step in plan["steps"]])

    def test_timeline_plan_switches_to_taiyin_before_duel_when_available(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["紫微", "太阴", "贪狼"],
                "available_stars_source": "observe",
                "fixed_star": "紫微",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "斗法", "kind": "farm", "start_at": now + 60, "end_at": now + 3600, "weight": 8, "reason": "斗法窗口"},
                ],
                config={
                    "auto_set_star_enabled": True,
                    "auto_predict_enabled": True,
                    "timeline_enabled": True,
                },
            )

        self.assertEqual("need_set_star", plan["stage"])
        self.assertEqual(("set_star", "太阴"), (plan["steps"][0]["action"], plan["steps"][0]["arg"]))

    def test_timeline_plan_skips_star_switch_when_route_star_absent(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼", "太阴"],
                "available_stars_source": "observe",
                "fixed_star": "太阴",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "闭关", "kind": "farm", "start_at": now + 60, "end_at": now + 3600, "weight": 8, "reason": "闭关/出关窗口"},
                ],
                config={
                    "auto_set_star_enabled": True,
                    "auto_predict_enabled": True,
                    "timeline_enabled": True,
                },
            )

        self.assertEqual("need_predict", plan["stage"])
        self.assertNotIn("set_star", [step["action"] for step in plan["steps"]])
        self.assertEqual(("predict", "闭关"), (plan["steps"][0]["action"], plan["steps"][0]["arg"]))

    def test_timeline_plan_does_not_emit_strategy_steps_when_switches_are_closed(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "fixed_star": "贪狼",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 42,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "闭关", "kind": "farm", "start_at": now + 60, "end_at": now + 3600, "weight": 8},
                    {"route": "探索", "kind": "consume", "start_at": now + 120, "end_at": now + 240, "weight": 2},
                ],
                config={
                    "auto_predict_enabled": False,
                    "auto_change_fate_enabled": False,
                },
            )

        self.assertEqual("observe_only", plan["stage"])
        self.assertEqual([], plan["steps"])
        self.assertIn("自动推命关闭", plan["predict_reason"])
        self.assertIn("自动改命关闭", plan["change_reason"])

    def test_timeline_plan_blocks_route_switch_when_other_prediction_active(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "fixed_star": "太阴",
                "current_prediction": "闭关",
                "current_prediction_until": now + 1800,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "探索", "kind": "farm", "start_at": now + 60, "end_at": now + 1800, "weight": 8, "reason": "探索主窗口"},
                ],
            )

        self.assertEqual("prediction_conflict", plan["stage"])
        self.assertTrue(plan["blocked_by_conflict"])
        self.assertEqual(now + 1800, plan["blocked_until"])
        self.assertIn("已有 闭关 推命", plan["predict_reason"])

    def test_timeline_plan_blocks_prediction_override_even_when_configured(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "fixed_star": "贪狼",
                "current_prediction": "闭关",
                "current_prediction_until": now + 1800,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 3,
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[
                    {"route": "炼制", "kind": "farm", "start_at": now, "end_at": now + 300, "weight": 8, "reason": "炼制攒点"},
                ],
                config={"timeline_enabled": True, "auto_predict_enabled": True, "allow_prediction_override_enabled": True},
            )

        self.assertTrue(plan["blocked_by_conflict"])
        self.assertEqual("prediction_conflict", plan["stage"])
        self.assertEqual([], plan["steps"])
        self.assertIn("已有 闭关 推命", plan["predict_reason"])


class TianxingTimelineSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        self.identity_id = 2103
        state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(
            self.identity_id,
            username="tianxing_timeline",
            label="tianxing_timeline",
            sect_name="天星宗",
        )

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _prepare_timeline_identity(self, now, *, tianji_value=12, auto_change=False, dry_run=False):
        state_module.state["tianxing_enabled"] = True
        state_module.state["tianxing_observation"] = {
            "last_observed_at": now - 60,
            "available_stars": ["贪狼", "太阴"],
            "fixed_star": "贪狼",
            "current_prediction": "",
            "current_prediction_until": 0,
            "current_change": "",
            "current_change_until": 0,
            "tianji_value": tianji_value,
            "calamity_count": 0,
        }
        state_module.state["tianxing_timeline_state"] = {}
        state_module.state["tianxing_auto_config"] = {
            "timeline_enabled": True,
            "timeline_dry_run_enabled": dry_run,
            "auto_predict_enabled": True,
            "auto_change_fate_enabled": auto_change,
            "min_tianji_for_change": 6,
            "ack_timeout_sec": 15,
            "calibration_backoff_sec": 60,
            "status_backoff_hours": 1,
        }

    def _farm_windows(self, now):
        return [
            {"route": "闭关", "kind": "farm", "start_at": now + 60, "end_at": now + 3600, "weight": 8},
        ]

    async def test_timeline_set_star_confirmation_schedules_prompt_replan(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["贪狼", "太阴"],
                "available_stars_source": "observe",
                "fixed_star": "",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 3,
                "auto_next_time": now + 3600,
            }
            state_module.state["tianxing_timeline_state"] = {
                "phase": "sent_waiting_ack",
                "active_step_index": 0,
                "active_step": {
                    "action": "set_star",
                    "arg": "贪狼",
                    "command": ".定命 贪狼",
                    "status": "sent_waiting_ack",
                    "send_msg_id": 9101,
                    "sent_at": now,
                    "ack_due_at": now + 90,
                },
                "steps": [{
                    "action": "set_star",
                    "arg": "贪狼",
                    "command": ".定命 贪狼",
                    "status": "sent_waiting_ack",
                    "send_msg_id": 9101,
                    "sent_at": now,
                    "ack_due_at": now + 90,
                }],
            }

            changed = tianxing.apply_tianxing_passive("你将今日命轨定在【贪狼】。", now=now + 5, family="tianxing_set_star")
            observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertTrue(changed)
        self.assertEqual("贪狼", observed["fixed_star"])
        self.assertEqual("state_confirmed", timeline["phase"])
        self.assertLessEqual(observed["auto_next_time"], now + 65)

    async def test_timeline_dry_run_records_steps_without_sending(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=True, dry_run=True)
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command") as send_mock:
                result = await tianxing.run_tianxing_timeline_scheduler(
                    now,
                    windows=[
                        {"route": "闭关", "kind": "farm", "start_at": now + 60, "end_at": now + 3600, "weight": 8},
                        {"route": "探索", "kind": "consume", "start_at": now + 120, "end_at": now + 240, "weight": 2},
                    ],
                )
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        send_mock.assert_not_called()
        self.assertEqual("dry_run", result["phase"])
        self.assertEqual("dry_run", timeline["phase"])
        self.assertEqual(["dry_run", "dry_run", "dry_run"], [step["status"] for step in timeline["steps"]])
        self.assertEqual(".推命 闭关", timeline["steps"][0]["command"])
        self.assertEqual(".改命 探索", timeline["steps"][1]["command"])

    async def test_timeline_waits_for_state_confirmation_before_releasing_route(self):
        now = 1_780_000_000.0
        first_msg = SimpleNamespace(id=9101, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command", return_value=first_msg) as send_mock:
                result = await tianxing.run_tianxing_timeline_scheduler(now, windows=self._farm_windows(now))

            self.assertEqual("sent_waiting_ack", result["phase"])
            send_mock.assert_awaited_once()
            self.assertEqual(".推命 闭关", send_mock.await_args.args[0])
            self.assertFalse(tianxing.is_tianxing_route_released("闭关", now=now + 1))

            with patch.object(tianxing, "save_state"):
                waiting = await tianxing.run_tianxing_timeline_scheduler(now + 5, windows=self._farm_windows(now))
            self.assertEqual("sent_waiting_ack", waiting["phase"])
            self.assertFalse(tianxing.is_tianxing_route_released("闭关", now=now + 5))

            tianxing.apply_tianxing_passive("你为【闭关】推下一段命数，司命盘微微转动。", now=now + 6)
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])
            self.assertEqual("state_confirmed", timeline["phase"])

            with patch.object(tianxing, "save_state"):
                advanced = await tianxing.run_tianxing_timeline_scheduler(now + 7, windows=self._farm_windows(now))
            self.assertEqual("downstream_released", advanced["phase"])
            self.assertTrue(tianxing.is_tianxing_route_released("闭关", now=now + 7))

            with patch.object(tianxing, "save_state"):
                released = await tianxing.run_tianxing_timeline_scheduler(now + 8, windows=self._farm_windows(now))
            self.assertEqual("completed", released["phase"])
            self.assertTrue(tianxing.is_tianxing_route_released("闭关", now=now + 8))

    async def test_timeline_send_without_message_id_waits_for_calibration_without_resend(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command", return_value=None) as send_mock:
                result = await tianxing.run_tianxing_timeline_scheduler(now, windows=self._farm_windows(now))
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command") as resend_mock:
                waiting = await tianxing.run_tianxing_timeline_scheduler(now + 30, windows=self._farm_windows(now))

        send_mock.assert_awaited_once()
        resend_mock.assert_not_called()
        self.assertEqual("ack_timeout", result["phase"])
        self.assertEqual("ack_timeout", timeline["active_step"]["status"])
        self.assertEqual("ack_timeout", waiting["phase"])
        self.assertFalse(tianxing.is_tianxing_route_released("闭关", now=now + 1))

    async def test_stale_unsent_ack_timeout_replans_when_consume_route_changes(self):
        now = 1_780_000_000.0
        msg = SimpleNamespace(id=9102, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=True, dry_run=False)
            state_module.state["tianxing_observation"].update({
                "current_change": "探索",
                "current_change_until": now + 12 * 3600,
            })
            state_module.state["tianxing_timeline_state"] = {
                "phase": "ack_timeout",
                "route": "炼制",
                "blocked_until": now + 3600,
                "last_error": "天星前置命令发送队列超时，等待查盘校准；不重复发送。",
                "active_step_index": 0,
                "active_step": {
                    "action": "predict",
                    "arg": "炼制",
                    "route": "炼制",
                    "command": ".推命 炼制",
                    "status": "ack_timeout",
                    "send_msg_id": 0,
                    "calibration_due_at": now + 3600,
                },
                "steps": [{
                    "action": "predict",
                    "arg": "炼制",
                    "route": "炼制",
                    "command": ".推命 炼制",
                    "status": "ack_timeout",
                    "send_msg_id": 0,
                    "calibration_due_at": now + 3600,
                }],
            }

            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command", return_value=msg) as send_mock:
                result = await tianxing.run_tianxing_timeline_scheduler(
                    now,
                    windows=[{
                        "route": "探索",
                        "kind": "consume",
                        "start_at": now,
                        "end_at": now + 60,
                        "weight": 10,
                        "reason": "野外历练",
                        "require_change_fate": True,
                    }],
                )
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertEqual("sent_waiting_ack", result["phase"])
        send_mock.assert_awaited_once()
        self.assertEqual(".推命 探索", send_mock.await_args.args[0])
        self.assertEqual("探索", timeline["route"])
        self.assertEqual("predict", timeline["active_step"]["action"])
        self.assertEqual("探索", timeline["active_step"]["arg"])

    async def test_new_timeline_plan_preserves_craft_farm_accounting(self):
        now = 1_780_000_000.0
        first_msg = SimpleNamespace(id=9101, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            state_module.state["tianxing_observation"].update({
                "fixed_star": "贪狼",
                "current_prediction": "",
                "current_prediction_until": 0,
                "tianji_value": 10,
            })
            state_module.state["tianxing_timeline_state"] = {
                "craft_farm": {
                    "phase": "ready",
                    "started_at": now - 600,
                    "target_tianji": 42,
                    "start_tianji": 8,
                    "estimated_tianji": 10,
                    "daily_limit": 42,
                    "daily_count": 2,
                    "success_count": 2,
                    "hit_count": 2,
                    "last_item": "玄铁剑",
                    "last_msg_id": 9002,
                }
            }
            windows = [{"route": "炼制", "kind": "farm", "start_at": now, "end_at": now + 3600, "weight": 8}]
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command", return_value=first_msg):
                result = await tianxing.run_tianxing_timeline_scheduler(now, windows=windows)
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertEqual("sent_waiting_ack", result["phase"])
        self.assertEqual(2, timeline["craft_farm"]["daily_count"])
        self.assertEqual(2, timeline["craft_farm"]["hit_count"])
        self.assertEqual(10, timeline["craft_farm"]["estimated_tianji"])

    async def test_timeline_sending_state_blocks_parallel_resend(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            state_module.state["tianxing_timeline_state"] = {
                "plan_id": "tianxing-timeline-test",
                "phase": "sending",
                "route": "闭关",
                "active_step_index": 0,
                "active_step": {
                    "action": "predict",
                    "arg": "闭关",
                    "route": "闭关",
                    "command": ".推命 闭关",
                    "status": "sending",
                    "send_started_at": now - 10,
                    "ack_due_at": now + 80,
                },
                "steps": [{
                    "action": "predict",
                    "arg": "闭关",
                    "route": "闭关",
                    "command": ".推命 闭关",
                    "status": "sending",
                    "send_started_at": now - 10,
                    "ack_due_at": now + 80,
                }],
            }
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command") as send_mock:
                result = await tianxing.run_tianxing_timeline_scheduler(now, windows=self._farm_windows(now))

        send_mock.assert_not_called()
        self.assertEqual("sending", result["phase"])
        self.assertFalse(tianxing.is_tianxing_route_released("闭关", now=now + 1))

    def test_timeline_set_star_rejection_replans_to_observe(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            state_module.state["tianxing_timeline_state"] = {
                "plan_id": "tianxing-timeline-test",
                "phase": "sent_waiting_ack",
                "route": "闭关",
                "active_step_index": 0,
                "active_step": {
                    "action": "set_star",
                    "arg": "贪狼",
                    "command": ".定命 贪狼",
                    "status": "sent_waiting_ack",
                    "send_msg_id": 9101,
                    "sent_at": now,
                    "ack_due_at": now + 90,
                },
                "steps": [{
                    "action": "set_star",
                    "arg": "贪狼",
                    "command": ".定命 贪狼",
                    "status": "sent_waiting_ack",
                    "send_msg_id": 9101,
                    "sent_at": now,
                    "ack_due_at": now + 90,
                }],
            }

            changed = tianxing.apply_tianxing_passive(
                "此命星并未在你今日观命结果中显化，请先 .观命。",
                now=now + 1,
                family="tianxing_set_star",
            )
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])
            observed_after_reject = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])
            plan = tianxing.build_tianxing_timeline_plan(
                now=now + 2,
                windows=self._farm_windows(now + 2),
                config={"timeline_enabled": True, "auto_observe_enabled": True, "auto_set_star_enabled": True, "auto_predict_enabled": True},
            )

        self.assertTrue(changed)
        self.assertEqual("blocked_replan", timeline["phase"])
        self.assertEqual([], observed_after_reject["available_stars"])
        self.assertEqual([("observe", "")], [(step["action"], step["arg"]) for step in plan["steps"]])

    def test_timeline_predict_cooldown_same_route_confirms_existing_prediction(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            state_module.state["tianxing_timeline_state"] = {
                "plan_id": "tianxing-timeline-test",
                "phase": "sent_waiting_ack",
                "route": "闭关",
                "active_step_index": 0,
                "active_step": {
                    "action": "predict",
                    "arg": "闭关",
                    "route": "闭关",
                    "command": ".推命 闭关",
                    "status": "sent_waiting_ack",
                    "send_msg_id": 9101,
                    "sent_at": now,
                    "ack_due_at": now + 90,
                },
                "steps": [{
                    "action": "predict",
                    "arg": "闭关",
                    "route": "闭关",
                    "command": ".推命 闭关",
                    "status": "sent_waiting_ack",
                    "send_msg_id": 9101,
                    "sent_at": now,
                    "ack_due_at": now + 90,
                }],
            }

            changed = tianxing.apply_tianxing_passive(
                "你已有一道关于 【闭关】 的推命尚未应验，还需等待 7小时33分钟。",
                now=now + 1,
                family="tianxing_predict",
            )
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertTrue(changed)
        self.assertEqual("state_confirmed", timeline["phase"])
        self.assertEqual("confirmed_existing_prediction", timeline["active_step"]["status"])
        self.assertFalse(tianxing.is_tianxing_route_released("闭关", now=now + 1))

    def test_timeline_predict_cooldown_conflict_blocks_craft_without_downstream_release(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            state_module.state["tianxing_auto_config"] = {
                "timeline_enabled": True,
                "auto_predict_enabled": True,
                "allow_prediction_override_enabled": True,
                "farm_route": "炼制",
                "farm_window_enabled": True,
                "farm_window_start": time.strftime("%H:%M", time.localtime(now)),
                "farm_window_duration_min": 60,
                "target_tianji_daily": 42,
                "craft_farm_enabled": True,
                "craft_farm_dry_run_enabled": False,
                "craft_farm_daily_limit": 42,
                "craft_farm_item": "玄铁剑",
            }
            state_module.state["tianxing_timeline_state"] = {
                "plan_id": "tianxing-timeline-test",
                "phase": "sent_waiting_ack",
                "route": "炼制",
                "active_step_index": 0,
                "active_step": {
                    "action": "predict",
                    "arg": "炼制",
                    "route": "炼制",
                    "command": ".推命 炼制",
                    "status": "sent_waiting_ack",
                    "send_msg_id": 9102,
                    "sent_at": now,
                    "ack_due_at": now + 90,
                },
                "steps": [{
                    "action": "predict",
                    "arg": "炼制",
                    "route": "炼制",
                    "command": ".推命 炼制",
                    "status": "sent_waiting_ack",
                    "send_msg_id": 9102,
                    "sent_at": now,
                    "ack_due_at": now + 90,
                }],
                "craft_farm": {
                    "phase": "timeline_waiting",
                    "started_at": now - 5,
                    "target_tianji": 42,
                    "daily_limit": 42,
                    "estimated_tianji": 3,
                },
            }

            changed = tianxing.apply_tianxing_passive(
                "你已有一道关于 【闭关】 的推命尚未应验，还需等待 7小时33分钟。",
                now=now + 1,
                family="tianxing_predict",
            )
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])
            craft = timeline["craft_farm"]
            plan = tianxing.build_tianxing_craft_farm_plan(
                now=now + 2,
                config=state_module.state["tianxing_auto_config"],
            )

        self.assertTrue(changed)
        self.assertEqual("prediction_conflict", timeline["phase"])
        self.assertEqual({}, timeline["active_step"])
        self.assertFalse(tianxing.is_tianxing_route_released("炼制", now=now + 2))
        self.assertEqual("prediction_conflict", craft["phase"])
        self.assertEqual("waiting_prediction_conflict", plan["stage"])
        self.assertEqual("", plan["command"])

    async def test_timeline_replans_old_craft_conflict_for_explore_consume_window(self):
        now = 1_780_000_000.0
        sent_msg = SimpleNamespace(id=9201, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, tianji_value=24, auto_change=True, dry_run=False)
            state_module.state["tianxing_auto_config"]["min_tianji_for_change"] = 3
            state_module.state["tianxing_observation"].update({
                "current_prediction": "探索",
                "current_prediction_until": now + 3600,
                "current_change": "",
                "current_change_until": 0,
            })
            state_module.state["tianxing_timeline_state"] = {
                "phase": "prediction_conflict",
                "route": "炼制",
                "blocked_until": now + 3600,
                "last_error": "已有 探索 推命尚未应验，不能切到 炼制。",
            }
            windows = [{
                "route": "探索",
                "kind": "consume",
                "start_at": now,
                "end_at": now + 60,
                "weight": 10,
                "reason": "野外历练",
                "require_change_fate": True,
            }]
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(tianxing, "save_state"),
            ):
                result = await tianxing.run_tianxing_timeline_scheduler(now, windows=windows)
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        send_mock.assert_awaited_once()
        self.assertEqual(".改命 探索", send_mock.await_args.args[0])
        self.assertEqual("sent_waiting_ack", result["phase"])
        self.assertEqual("sent_waiting_ack", timeline["phase"])
        self.assertEqual("探索", timeline["route"])
        self.assertEqual("change_fate", timeline["active_step"]["action"])

    async def test_timeline_releases_fresh_panel_prediction_without_probe(self):
        now = 1_780_000_000.0
        windows = [{"route": "炼制", "kind": "farm", "start_at": now, "end_at": now + 3600, "weight": 8}]
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            state_module.state["tianxing_observation"].update({
                "last_action": "天机盘",
                "last_result": "panel",
                "last_observed_at": now,
                "current_prediction": "炼制",
                "current_prediction_until": now + 3600,
            })
            state_module.state["tianxing_timeline_state"] = {
                "craft_farm": {
                    "audit": [{"ts": now - 120, "event": "craft_result", "result": "prediction_hit"}],
                }
            }
            with (
                patch.object(tianxing, "save_state"),
                patch.object(tianxing, "send_game_command") as send_mock,
            ):
                result = await tianxing.run_tianxing_timeline_scheduler(now, windows=windows)
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertEqual("downstream_released", result["phase"])
        self.assertEqual("release_downstream", timeline["active_step"]["action"])
        self.assertTrue(tianxing.is_tianxing_route_released("炼制", now=now + 1))
        send_mock.assert_not_called()

    def test_passive_panel_closes_matching_predict_and_panel_guards(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
        action_guard.note_sent(".推命 炼制", self.identity_id, 9103, sent_at=now - 10)
        action_guard.note_sent(".天机盘", self.identity_id, 9104, sent_at=now - 5)
        self.assertIn("tianxing_predict", action_guard.get_action_guard_sessions(self.identity_id))
        self.assertIn("tianxing_panel", action_guard.get_action_guard_sessions(self.identity_id))

        with state_module.use_identity(self.identity_id):
            changed = tianxing.apply_tianxing_passive(
                "【天机盘】\n今日可选命星: 【紫微】、【贪狼】\n今日已定命星: 【贪狼】\n当前推命: 炼制（剩余 7小时）\n当前改命: 无\n天机值: 23\n逆命劫: 0\n命中 / 落空 / 改命: 29 / 1 / 2",
                now=now,
                family="tianxing_panel",
            )

        self.assertTrue(changed)
        sessions = action_guard.get_action_guard_sessions(self.identity_id)
        self.assertNotIn("tianxing_predict", sessions)
        self.assertNotIn("tianxing_panel", sessions)

    async def test_scheduler_closes_matching_predict_guard_from_existing_panel_state(self):
        now = 1_780_000_000.0
        windows = [{"route": "炼制", "kind": "farm", "start_at": now, "end_at": now + 3600, "weight": 8}]
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            state_module.state["tianxing_observation"].update({
                "last_action": "天机盘",
                "last_result": "panel",
                "last_observed_at": now - 1,
                "current_prediction": "炼制",
                "current_prediction_until": now + 3600,
            })
        action_guard.note_sent(".推命 炼制", self.identity_id, 9103, sent_at=now - 10)

        with state_module.use_identity(self.identity_id):
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command"):
                result = await tianxing.run_tianxing_timeline_scheduler(now, windows=windows)

        self.assertEqual("downstream_released", result["phase"])
        self.assertNotIn("tianxing_predict", action_guard.get_action_guard_sessions(self.identity_id))

    def test_timeline_releases_recent_predict_after_last_craft(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            state_module.state["tianxing_observation"].update({
                "last_action": "推命",
                "last_result": "success",
                "last_route": "炼制",
                "last_observed_at": now - 10,
                "current_prediction": "炼制",
                "current_prediction_until": now + 3600,
            })
            state_module.state["tianxing_timeline_state"] = {
                "craft_farm": {
                    "audit": [{"ts": now - 120, "event": "craft_result", "result": "prediction_hit"}],
                }
            }
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[{"route": "炼制", "kind": "farm", "start_at": now, "end_at": now + 3600, "weight": 8}],
                config=state_module.state["tianxing_auto_config"],
            )

        self.assertEqual("ready_prediction", plan["stage"])
        self.assertEqual(["release_downstream"], [step["action"] for step in plan["steps"]])

    def test_consume_window_requiring_change_does_not_release_on_prediction_only(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, tianji_value=2, auto_change=True, dry_run=False)
            state_module.state["tianxing_observation"].update({
                "last_action": "推命",
                "last_result": "success",
                "last_route": "探索",
                "current_prediction": "探索",
                "current_prediction_until": now + 3600,
            })
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[{
                    "route": "探索",
                    "kind": "consume",
                    "start_at": now,
                    "end_at": now + 60,
                    "weight": 10,
                    "reason": "探寻裂缝",
                    "require_change_fate": True,
                }],
                config=state_module.state["tianxing_auto_config"],
            )

        self.assertEqual("need_tianji_for_change", plan["stage"])
        self.assertEqual("", plan["release_route"])
        self.assertEqual([], [step["action"] for step in plan["steps"]])
        self.assertIn("天机值不足", plan["predict_reason"])

    def test_consume_window_adds_change_fate_when_stale_change_expired(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, tianji_value=9, auto_change=True, dry_run=False)
            state_module.state["tianxing_observation"].update({
                "last_action": "推命",
                "last_result": "success",
                "last_route": "探索",
                "current_prediction": "探索",
                "current_prediction_until": now + 3600,
                "current_change": "闭关",
                "current_change_until": now - 60,
            })
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[{
                    "route": "探索",
                    "kind": "consume",
                    "start_at": now,
                    "end_at": now + 60,
                    "weight": 10,
                    "reason": "野外历练",
                    "require_change_fate": True,
                }],
                config=state_module.state["tianxing_auto_config"],
            )

        self.assertEqual("ready_prediction", plan["stage"])
        self.assertEqual("探索", plan["recommended_change_route"])
        self.assertEqual("探索", plan["release_route"])
        self.assertEqual(
            [("change_fate", "探索"), ("release_downstream", "探索")],
            [(step["action"], step["arg"]) for step in plan["steps"]],
        )
        self.assertEqual("change_fate", plan["steps"][-1]["release_basis"])

    async def test_scheduler_replans_stale_empty_ready_prediction_for_required_change(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, tianji_value=9, auto_change=True, dry_run=False)
            state_module.state["tianxing_observation"].update({
                "last_action": "推命",
                "last_result": "success",
                "last_route": "探索",
                "current_prediction": "探索",
                "current_prediction_until": now + 3600,
                "current_change": "闭关",
                "current_change_until": now - 60,
            })
            state_module.state["tianxing_timeline_state"] = {
                "plan_id": "old-ready",
                "phase": "ready_prediction",
                "route": "探索",
                "reason": "探索 推命已由近期真实回复确认，无需重复押注。",
                "steps": [],
                "active_step_index": -1,
                "active_step": {},
                "blocked_until": now + 3600,
            }
            with patch.object(tianxing, "save_state"), patch.object(
                tianxing,
                "send_game_command",
                new=AsyncMock(return_value=SimpleNamespace(id=12345, sent_at=now + 1)),
            ) as send_mock:
                result = await tianxing.run_tianxing_timeline_scheduler(
                    now,
                    windows=[{
                        "route": "探索",
                        "kind": "consume",
                        "start_at": now,
                        "end_at": now + 60,
                        "weight": 10,
                        "reason": "野外历练",
                        "require_change_fate": True,
                    }],
                )
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertEqual("sent_waiting_ack", result["phase"])
        send_mock.assert_awaited_once()
        self.assertEqual(".改命 探索", send_mock.await_args.args[0])
        self.assertEqual("change_fate", timeline["active_step"]["action"])
        self.assertEqual("探索", timeline["active_step"]["arg"])
        self.assertEqual("sent_waiting_ack", timeline["active_step"]["status"])

    def test_consume_window_lacking_tianji_does_not_override_prediction(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, tianji_value=2, auto_change=True, dry_run=False)
            state_module.state["tianxing_observation"].update({
                "current_prediction": "炼制",
                "current_prediction_until": now + 3600,
            })
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[{
                    "route": "探索",
                    "kind": "consume",
                    "start_at": now,
                    "end_at": now + 60,
                    "weight": 10,
                    "reason": "探寻裂缝",
                    "require_change_fate": True,
                }],
                config=dict(
                    state_module.state["tianxing_auto_config"],
                    allow_prediction_override_enabled=True,
                    min_tianji_for_change=3,
                ),
            )

        self.assertEqual("need_tianji_for_change", plan["stage"])
        self.assertFalse(plan["blocked_by_conflict"])
        self.assertEqual([], [(step["action"], step["arg"]) for step in plan["steps"]])
        self.assertIn("天机值不足", plan["predict_reason"])

    def test_consume_window_with_tianji_blocks_conflicting_prediction(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, tianji_value=3, auto_change=True, dry_run=False)
            state_module.state["tianxing_observation"].update({
                "current_prediction": "炼制",
                "current_prediction_until": now + 3600,
            })
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[{
                    "route": "探索",
                    "kind": "consume",
                    "start_at": now,
                    "end_at": now + 60,
                    "weight": 10,
                    "reason": "探寻裂缝",
                    "require_change_fate": True,
                }],
                config=dict(
                    state_module.state["tianxing_auto_config"],
                    allow_prediction_override_enabled=True,
                    min_tianji_for_change=3,
                ),
            )

        self.assertEqual("prediction_conflict", plan["stage"])
        self.assertTrue(plan["blocked_by_conflict"])
        self.assertEqual([], [(step["action"], step["arg"]) for step in plan["steps"]])
        self.assertIn("已有 炼制 推命", plan["predict_reason"])

    async def test_timeline_need_tianji_for_change_does_not_set_long_global_block(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, tianji_value=2, auto_change=True, dry_run=False)
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command") as send_mock:
                result = await tianxing.run_tianxing_timeline_scheduler(
                    now,
                    windows=[{
                        "route": "探索",
                        "kind": "consume",
                        "start_at": now,
                        "end_at": now + 60,
                        "weight": 10,
                        "reason": "探寻裂缝",
                        "require_change_fate": True,
                    }],
                )
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertEqual("need_tianji_for_change", result["phase"])
        self.assertEqual("need_tianji_for_change", timeline["phase"])
        self.assertLessEqual(timeline["blocked_until"], now)
        self.assertIn("天机值不足", timeline["last_error"])
        send_mock.assert_not_called()

    async def test_timeline_observe_only_does_not_set_long_global_block_for_consume_window(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, tianji_value=9, auto_change=False, dry_run=False)
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command") as send_mock:
                result = await tianxing.run_tianxing_timeline_scheduler(
                    now,
                    windows=[{
                        "route": "探索",
                        "kind": "consume",
                        "start_at": now,
                        "end_at": now + 60,
                        "weight": 10,
                        "reason": "野外历练",
                    }],
                )
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertEqual("observe_only", result["phase"])
        self.assertLessEqual(timeline["blocked_until"], now)
        send_mock.assert_not_called()

    async def test_timeline_change_fate_conflict_does_not_set_long_global_block(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, tianji_value=9, auto_change=True, dry_run=False)
            state_module.state["tianxing_observation"]["current_change"] = "闭关"
            state_module.state["tianxing_observation"]["current_change_until"] = now + 12 * 3600
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command") as send_mock:
                result = await tianxing.run_tianxing_timeline_scheduler(
                    now,
                    windows=[{
                        "route": "探索",
                        "kind": "consume",
                        "start_at": now,
                        "end_at": now + 60,
                        "weight": 10,
                        "reason": "探寻裂缝",
                        "require_change_fate": True,
                    }],
                )
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertEqual("sent_waiting_ack", result["phase"])
        self.assertEqual("sent_waiting_ack", timeline["phase"])
        self.assertEqual("predict", timeline["active_step"]["action"])
        self.assertEqual("探索", timeline["active_step"]["arg"])
        send_mock.assert_awaited_once()
        self.assertEqual(".推命 探索", send_mock.await_args.args[0])

    def test_consume_window_requiring_change_releases_only_with_change_fate(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, tianji_value=9, auto_change=True, dry_run=False)
            state_module.state["tianxing_observation"].update({
                "current_prediction": "探索",
                "current_prediction_until": now + 3600,
                "current_change": "探索",
                "current_change_until": now + 12 * 3600,
            })
            plan = tianxing.build_tianxing_timeline_plan(
                now=now,
                windows=[{
                    "route": "探索",
                    "kind": "consume",
                    "start_at": now,
                    "end_at": now + 60,
                    "weight": 10,
                    "reason": "探寻裂缝",
                    "require_change_fate": True,
                }],
                config=state_module.state["tianxing_auto_config"],
            )

        self.assertEqual("探索", plan["release_route"])
        self.assertEqual(["release_downstream"], [step["action"] for step in plan["steps"]])
        self.assertEqual("change_fate", plan["steps"][0]["release_basis"])

    async def test_timeline_confirmed_prediction_immediately_releases_downstream(self):
        now = 1_780_000_000.0
        sent_at = now - 2
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, tianji_value=9, auto_change=True, dry_run=False)
            state_module.state["tianxing_observation"].update({
                "last_observed_at": now - 1,
                "current_prediction": "探索",
                "current_prediction_until": now + 3600,
                "current_change": "探索",
                "current_change_until": now + 12 * 3600,
            })
            state_module.state["tianxing_timeline_state"] = {
                "plan_id": "tianxing-timeline-探索-test",
                "phase": "sent_waiting_ack",
                "route": "探索",
                "active_step_index": 0,
                "active_step": {
                    "id": "predict:探索:test",
                    "action": "predict",
                    "arg": "探索",
                    "route": "探索",
                    "command": ".推命 探索",
                    "status": "sent_waiting_ack",
                    "send_msg_id": 11244533,
                    "sent_at": sent_at,
                    "ack_due_at": now + 30,
                },
                "steps": [
                    {
                        "id": "predict:探索:test",
                        "action": "predict",
                        "arg": "探索",
                        "route": "探索",
                        "command": ".推命 探索",
                        "status": "sent_waiting_ack",
                        "send_msg_id": 11244533,
                        "sent_at": sent_at,
                        "ack_due_at": now + 30,
                    },
                    {
                        "id": "release_downstream:探索:test",
                        "action": "release_downstream",
                        "arg": "探索",
                        "route": "探索",
                        "status": "pending",
                        "release_basis": "change_fate",
                    },
                ],
            }

            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command") as send_mock:
                result = await tianxing.run_tianxing_timeline_scheduler(now)
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertEqual("downstream_released", result["phase"])
        self.assertEqual("downstream_released", timeline["phase"])
        self.assertEqual("released", timeline["active_step"]["status"])
        self.assertEqual("change_fate", timeline["released_routes"]["探索"]["basis"])
        send_mock.assert_not_called()

    async def test_timeline_ack_timeout_schedules_panel_calibration_without_releasing(self):
        now = 1_780_000_000.0
        first_msg = SimpleNamespace(id=9101, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command", return_value=first_msg):
                await tianxing.run_tianxing_timeline_scheduler(now, windows=self._farm_windows(now))

            with patch.object(tianxing, "save_state"):
                timed_out = await tianxing.run_tianxing_timeline_scheduler(now + 16, windows=self._farm_windows(now))
            self.assertEqual("ack_timeout", timed_out["phase"])
            self.assertFalse(tianxing.is_tianxing_route_released("闭关", now=now + 16))

            with patch.object(tianxing, "save_state"):
                calibrating = await tianxing.run_tianxing_timeline_scheduler(now + 77, windows=self._farm_windows(now))
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertEqual("calibrating", calibrating["phase"])
        self.assertEqual("panel", timeline["active_step"]["action"])
        self.assertEqual(".天机盘", timeline["active_step"]["command"])
        self.assertFalse(tianxing.is_tianxing_route_released("闭关", now=now + 77))

    async def test_timeline_panel_calibration_timeout_replans_without_repeat_panel(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            state_module.state["tianxing_timeline_state"] = {
                "plan_id": "tianxing-timeline-test",
                "phase": "ack_timeout",
                "route": "炼制",
                "active_step_index": 1,
                "active_step": {
                    "action": "panel",
                    "arg": "",
                    "route": "",
                    "command": ".天机盘",
                    "status": "ack_timeout",
                    "send_msg_id": 9104,
                    "sent_at": now - 120,
                    "ack_due_at": now - 30,
                    "calibration_due_at": now - 1,
                    "terminal_after_confirm": True,
                },
                "steps": [
                    {
                        "action": "predict",
                        "arg": "炼制",
                        "route": "炼制",
                        "command": ".推命 炼制",
                        "status": "ack_timeout",
                    },
                    {
                        "action": "panel",
                        "arg": "",
                        "route": "",
                        "command": ".天机盘",
                        "status": "ack_timeout",
                        "send_msg_id": 9104,
                        "sent_at": now - 120,
                        "ack_due_at": now - 30,
                        "calibration_due_at": now - 1,
                        "terminal_after_confirm": True,
                    },
                ],
            }
            with patch.object(tianxing, "save_state"):
                result = await tianxing.run_tianxing_timeline_scheduler(now, windows=self._farm_windows(now))
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertEqual("blocked_replan", result["phase"])
        self.assertEqual({}, timeline["active_step"])
        self.assertEqual("calibration_timeout", timeline["steps"][1]["status"])
        self.assertIn("不连续查盘", timeline["last_error"])

    async def test_timeline_ignores_stale_reply_before_sent_at(self):
        now = 1_780_000_000.0
        first_msg = SimpleNamespace(id=9101, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command", return_value=first_msg):
                await tianxing.run_tianxing_timeline_scheduler(now, windows=self._farm_windows(now))

            tianxing.apply_tianxing_passive("你为【闭关】推下一段命数，司命盘微微转动。", now=now - 5)
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertEqual("sent_waiting_ack", timeline["phase"])
        self.assertEqual("sent_waiting_ack", timeline["active_step"]["status"])
        self.assertFalse(tianxing.is_tianxing_route_released("闭关", now=now + 1))

    async def test_timeline_late_reply_after_timeout_requires_calibration_replan_before_release(self):
        now = 1_780_000_000.0
        first_msg = SimpleNamespace(id=9101, sent_at=now)
        panel_msg = SimpleNamespace(id=9102, sent_at=now + 78)
        panel_text = (
            "【天机盘】\n"
            "今日可选命星: 【贪狼】、【太阴】\n"
            "今日已定命星: 【贪狼】\n"
            "当前推命: 闭关（剩余 7小时50分钟）\n"
            "当前改命: 无\n"
            "天机值: 12\n"
            "逆命劫: 0\n"
            "命中/落空/改命: 2/1/0"
        )
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command", return_value=first_msg):
                await tianxing.run_tianxing_timeline_scheduler(now, windows=self._farm_windows(now))

            with patch.object(tianxing, "save_state"):
                timed_out = await tianxing.run_tianxing_timeline_scheduler(now + 16, windows=self._farm_windows(now))
            self.assertEqual("ack_timeout", timed_out["phase"])

            tianxing.apply_tianxing_passive("你为【闭关】推下一段命数，司命盘微微转动。", now=now + 20)
            self.assertFalse(tianxing.is_tianxing_route_released("闭关", now=now + 20))
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])
            self.assertEqual("ack_timeout", timeline["phase"])

            with patch.object(tianxing, "save_state"):
                await tianxing.run_tianxing_timeline_scheduler(now + 77, windows=self._farm_windows(now))
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command", return_value=panel_msg) as send_mock:
                sent_panel = await tianxing.run_tianxing_timeline_scheduler(now + 78, windows=self._farm_windows(now))
            self.assertEqual("sent_waiting_ack", sent_panel["phase"])
            self.assertEqual(".天机盘", send_mock.await_args.args[0])

            tianxing.apply_tianxing_passive(panel_text, now=now + 79, family="tianxing_panel")
            with patch.object(tianxing, "save_state"):
                blocked = await tianxing.run_tianxing_timeline_scheduler(now + 80, windows=self._farm_windows(now))
            self.assertEqual("blocked_replan", blocked["phase"])
            self.assertFalse(tianxing.is_tianxing_route_released("闭关", now=now + 80))

            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command") as probe_send:
                released = await tianxing.run_tianxing_timeline_scheduler(now + 81, windows=self._farm_windows(now + 81))
            probe_send.assert_not_called()

        self.assertEqual("downstream_released", released["phase"])
        self.assertTrue(tianxing.is_tianxing_route_released("闭关", now=now + 81))

    async def test_timeline_replans_consumed_empty_blocked_replan_when_due(self):
        now = 1_780_000_000.0
        first_msg = SimpleNamespace(id=9201, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            state_module.state["tianxing_timeline_state"] = {
                "plan_id": "tianxing-timeline-consumed",
                "phase": "blocked_replan",
                "route": "探索",
                "active_step_index": -1,
                "active_step": {},
                "steps": [],
                "blocked_until": now - 1,
                "last_error": "探索 放行已被下游动作消费，需重算时间线。",
            }

            with patch.object(tianxing, "save_state"), \
                 patch.object(tianxing, "send_game_command", return_value=first_msg) as send_mock:
                result = await tianxing.run_tianxing_timeline_scheduler(now, windows=self._farm_windows(now))
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        self.assertEqual("sent_waiting_ack", result["phase"])
        self.assertEqual("闭关", timeline["route"])
        self.assertEqual("predict", timeline["active_step"]["action"])
        self.assertEqual("闭关", timeline["active_step"]["arg"])
        self.assertEqual(".推命 闭关", send_mock.await_args.args[0])

    def test_ui_snapshot_exposes_timeline_state(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_timeline_identity(now, auto_change=False, dry_run=False)
            state_module.state["tianxing_timeline_state"] = {
                "plan_id": "tianxing-timeline-test",
                "phase": "sent_waiting_ack",
                "route": "闭关",
                "reason": "闭关 Farm 窗口",
                "created_at": now - 5,
                "updated_at": now,
                "deadline_at": now + 3600,
                "blocked_until": now + 60,
                "active_step_index": 0,
                "active_step": {
                    "action": "predict",
                    "arg": "闭关",
                    "route": "闭关",
                    "command": ".推命 闭关",
                    "family": "tianxing_predict",
                    "status": "sent_waiting_ack",
                    "sent_at": now,
                    "ack_due_at": now + 120,
                    "send_msg_id": 9101,
                },
                "steps": [
                    {
                        "action": "predict",
                        "arg": "闭关",
                        "route": "闭关",
                        "command": ".推命 闭关",
                        "family": "tianxing_predict",
                        "status": "sent_waiting_ack",
                    }
                ],
                "released_routes": {
                    "闭关": {"released_at": now + 10, "plan_id": "tianxing-timeline-test", "reason": "测试放行"}
                },
                "audit": [{"ts": now, "event": "sent_waiting_ack", "action": "predict", "arg": "闭关", "route": "闭关"}],
            }

        snapshot = ui.get_identity_ui_snapshot(self.identity_id)
        timeline = snapshot["tianxing"]["timeline"]

        self.assertEqual("sent_waiting_ack", timeline["phase"])
        self.assertEqual("闭关", timeline["route"])
        self.assertEqual(".推命 闭关", timeline["active_step"]["command"])
        self.assertEqual("sent_waiting_ack", timeline["active_step"]["status"])
        self.assertEqual(9101, timeline["active_step"]["send_msg_id"])
        self.assertEqual("闭关", timeline["released_routes"][0]["route"])
        self.assertEqual("sent_waiting_ack", timeline["audit"][0]["event"])


class TianxingRetreatFarmTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        self.identity_id = 2104
        state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(
            self.identity_id,
            username="tianxing_retreat",
            label="tianxing_retreat",
            sect_name="天星宗",
        )

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _active_config(self, now, **overrides):
        local_time = time.localtime(now)
        config = {
            "timeline_enabled": False,
            "retreat_farm_enabled": True,
            "retreat_farm_dry_run_enabled": False,
            "retreat_farm_allow_force_exit": False,
            "retreat_farm_allow_heqi_dan": False,
            "farm_route": "闭关",
            "farm_window_enabled": True,
            "farm_window_start": f"{local_time.tm_hour:02d}:{local_time.tm_min:02d}",
            "farm_window_duration_min": 5,
            "target_tianji_daily": 42,
        }
        config.update(overrides)
        return config

    def _prepare_identity(self, now, *, tianji_value=12):
        state_module.state["tianxing_enabled"] = True
        state_module.state["tianxing_observation"] = {
            "last_observed_at": now - 60,
            "available_stars": ["贪狼"],
            "fixed_star": "贪狼",
            "current_prediction": "",
            "current_prediction_until": 0,
            "current_change": "",
            "current_change_until": 0,
            "tianji_value": tianji_value,
            "calamity_count": 0,
        }
        state_module.state["tianxing_timeline_state"] = {}

    def test_retreat_farm_defaults_are_closed_and_dry_run(self):
        config = tianxing.normalize_tianxing_auto_config({})

        self.assertFalse(config["retreat_farm_enabled"])
        self.assertTrue(config["retreat_farm_dry_run_enabled"])
        self.assertFalse(config["retreat_farm_allow_force_exit"])
        self.assertFalse(config["retreat_farm_allow_heqi_dan"])
        self.assertFalse(config["retreat_farm_auto_exchange_heqi_dan"])
        self.assertEqual(10, config["retreat_farm_heqi_exchange_count"])
        self.assertFalse(config["retreat_farm_auto_donate_lingshi"])
        self.assertEqual(200, config["retreat_farm_donate_lingshi_count"])
        self.assertFalse(config["craft_farm_enabled"])
        self.assertTrue(config["craft_farm_dry_run_enabled"])
        self.assertEqual("玄铁剑", config["craft_farm_item"])
        self.assertEqual(42, config["craft_farm_daily_limit"])
        self.assertEqual("02:00-05:00,06:00-11:50,14:30-17:30,23:00-23:35", config["farm_windows_text"])
        self.assertEqual(120, config["craft_farm_interval_min_sec"])
        self.assertEqual(300, config["craft_farm_interval_max_sec"])
        self.assertTrue(config["craft_farm_off_window_enabled"])
        self.assertEqual(1800, config["craft_farm_off_window_interval_min_sec"])
        self.assertEqual(3600, config["craft_farm_off_window_interval_max_sec"])
        self.assertFalse(config["duel_route_enabled"])
        self.assertFalse(config["consume_conflicting_prediction_enabled"])
        self.assertFalse(config["route_special_star_enabled"])
        self.assertEqual(["太阴", "贪狼", "天府", "紫微"], config["star_priority"])

    def test_farm_window_defaults_cover_preferred_local_hours_and_skip_noon(self):
        config = tianxing.normalize_tianxing_auto_config({
            "farm_route": "炼制",
            "farm_window_enabled": True,
        })
        night = tianxing.build_tianxing_farm_window(now=local_ts(3), config=config, reason="test")
        morning = tianxing.build_tianxing_farm_window(now=local_ts(7), config=config, reason="test")
        noon = tianxing.build_tianxing_farm_window(now=local_ts(12), config=config, reason="test")
        afternoon = tianxing.build_tianxing_farm_window(now=local_ts(15, 30), config=config, reason="test")

        self.assertEqual(1, len(night))
        self.assertEqual("炼制", night[0]["route"])
        self.assertEqual(1, len(morning))
        self.assertEqual([], noon)
        self.assertEqual(1, len(afternoon))
        self.assertEqual(local_ts(14, 30), tianxing.next_tianxing_farm_window_start(now=local_ts(12), config=config))

    def test_legacy_single_window_and_fixed_interval_config_still_work(self):
        config = tianxing.normalize_tianxing_auto_config({
            "farm_route": "炼制",
            "farm_window_start": "04:10",
            "farm_window_duration_min": 20,
            "craft_farm_interval_sec": 600,
        })

        self.assertEqual("04:10-04:30", config["farm_windows_text"])
        self.assertEqual(600, config["craft_farm_interval_min_sec"])
        self.assertEqual(600, config["craft_farm_interval_max_sec"])
        self.assertTrue(tianxing.build_tianxing_farm_window(now=local_ts(4, 20), config=config, reason="test"))

    def test_old_default_twenty_second_interval_migrates_to_random_range(self):
        config = tianxing.normalize_tianxing_auto_config({
            "farm_route": "炼制",
            "craft_farm_interval_sec": 20,
        })

        self.assertEqual(120, config["craft_farm_interval_min_sec"])
        self.assertEqual(300, config["craft_farm_interval_max_sec"])

    def test_legacy_saved_random_range_migrates_to_current_default(self):
        config = tianxing.normalize_tianxing_auto_config({
            "farm_route": "炼制",
            "craft_farm_interval_min_sec": 180,
            "craft_farm_interval_max_sec": 420,
        })

        self.assertEqual(120, config["craft_farm_interval_min_sec"])
        self.assertEqual(300, config["craft_farm_interval_max_sec"])

    def test_craft_farm_outside_window_schedules_next_preferred_window(self):
        now = local_ts(12)
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=12)
            plan = tianxing.build_tianxing_craft_farm_plan(
                now=now,
                config={
                    "timeline_enabled": True,
                    "farm_route": "炼制",
                    "farm_window_enabled": True,
                    "farm_windows_text": "02:00-05:00,06:00-09:00,15:00-16:00",
                    "craft_farm_enabled": True,
                    "craft_farm_dry_run_enabled": False,
                    "craft_farm_off_window_enabled": False,
                    "target_tianji_daily": 42,
                },
            )

        self.assertEqual("outside_window", plan["stage"])
        self.assertTrue(plan["active"])
        self.assertEqual(local_ts(15), plan["next_time"])

    def test_craft_farm_outside_window_low_frequency_runs_when_enabled(self):
        now = local_ts(12)
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=12)
            state_module.state["tianxing_observation"]["current_prediction"] = "炼制"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 3600
            state_module.state["tianxing_timeline_state"] = {
                "released_routes": {
                    "炼制": {"released_at": now, "plan_id": "test", "reason": "炼制已放行", "basis": "prediction"}
                }
            }
            with patch.object(tianxing.random, "uniform", return_value=2400):
                plan = tianxing.build_tianxing_craft_farm_plan(
                    now=now,
                    config={
                        "timeline_enabled": True,
                        "farm_route": "炼制",
                        "farm_window_enabled": True,
                        "farm_windows_text": "02:00-05:00,06:00-09:00,15:00-16:00",
                        "craft_farm_enabled": True,
                        "craft_farm_dry_run_enabled": False,
                        "craft_farm_off_window_enabled": True,
                        "craft_farm_off_window_interval_min_sec": 1800,
                        "craft_farm_off_window_interval_max_sec": 3600,
                        "target_tianji_daily": 42,
                    },
                )

        self.assertEqual("send_craft", plan["stage"])
        self.assertTrue(plan["off_window"])
        self.assertEqual(".炼制 玄铁剑", plan["command"])
        self.assertEqual(now + 2400, plan["next_time"])
        self.assertIn("窗口外低频", plan["reason"])

    def test_craft_farm_outside_window_still_yields_to_explore_consume(self):
        now = local_ts(12)
        due_at = now + 240
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=12)
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = due_at
            plan = tianxing.build_tianxing_craft_farm_plan(
                now=now,
                config={
                    "timeline_enabled": True,
                    "farm_route": "炼制",
                    "farm_window_enabled": True,
                    "farm_windows_text": "02:00-05:00,06:00-09:00,15:00-16:00",
                    "craft_farm_enabled": True,
                    "craft_farm_dry_run_enabled": False,
                    "craft_farm_off_window_enabled": True,
                    "target_tianji_daily": 42,
                    "route_prepare_lead_sec": 300,
                    "min_tianji_for_change": 6,
                },
            )

        self.assertEqual("waiting_consume_window", plan["stage"])
        self.assertEqual("", plan["command"])
        self.assertIn("探寻裂缝", plan["reason"])

    def test_timeline_wake_respects_future_craft_wait(self):
        now = local_ts(12)
        config = tianxing.normalize_tianxing_auto_config({
            "timeline_enabled": True,
            "auto_predict_enabled": True,
            "craft_farm_enabled": True,
            "craft_farm_dry_run_enabled": False,
            "craft_farm_off_window_enabled": True,
            "farm_route": "炼制",
            "farm_windows_text": "02:00-05:00,06:00-09:00,15:00-16:00",
        })
        observed = {
            "last_observed_at": now - 60,
            "available_stars": ["太阴", "贪狼"],
            "available_stars_source": "observe",
            "available_stars_day": tianxing.get_day_key(now),
            "fixed_star": "太阴",
            "fixed_star_day": tianxing.get_day_key(now),
            "current_prediction": "",
            "current_prediction_until": 0,
            "current_change": "探索",
            "current_change_until": now + 12 * 3600,
            "tianji_value": 31,
        }
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_timeline_state"] = {
                "phase": "blocked_replan",
                "route": "探索",
                "craft_farm": {
                    "phase": "waiting",
                    "next_time": now + 3600,
                    "last_error": "探索消费窗口临近，炼制攒点让路。",
                },
            }
            should_wake = tianxing._should_wake_tianxing_timeline(observed, config, now)

        self.assertFalse(should_wake)

    def test_craft_farm_interval_uses_configured_random_range(self):
        now = local_ts(3)
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=12)
            state_module.state["tianxing_observation"]["current_prediction"] = "炼制"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 3600
            state_module.state["tianxing_timeline_state"] = {
                "released_routes": {
                    "炼制": {"released_at": now, "plan_id": "test", "reason": "炼制已放行", "basis": "prediction"}
                }
            }
            with patch.object(tianxing.random, "uniform", return_value=333):
                plan = tianxing.build_tianxing_craft_farm_plan(
                    now=now,
                    config={
                        "timeline_enabled": True,
                        "farm_route": "炼制",
                        "farm_window_enabled": True,
                        "farm_windows_text": "02:00-05:00,06:00-09:00,15:00-16:00",
                        "craft_farm_enabled": True,
                        "craft_farm_dry_run_enabled": False,
                        "craft_farm_interval_min_sec": 300,
                        "craft_farm_interval_max_sec": 600,
                        "target_tianji_daily": 42,
                    },
                )

        self.assertEqual("send_craft", plan["stage"])
        self.assertEqual(now + 333, plan["next_time"])

    def test_craft_farm_yields_to_upcoming_wild_training_consume_window(self):
        now = 1_780_000_000.0
        due_at = now + 240
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=12)
            state_module.state["wild_training_enabled"] = True
            state_module.state["next_wild_training_time"] = due_at
            plan = tianxing.build_tianxing_craft_farm_plan(
                now=now,
                config=self._active_config(
                    now,
                    timeline_enabled=True,
                    farm_route="炼制",
                    craft_farm_enabled=True,
                    craft_farm_dry_run_enabled=False,
                    craft_farm_item="玄铁剑",
                    craft_farm_daily_limit=42,
                    route_prepare_lead_sec=300,
                ),
            )

        self.assertEqual("waiting_consume_window", plan["stage"])
        self.assertTrue(plan["active"])
        self.assertFalse(plan["takeover"])
        self.assertEqual("", plan["command"])
        self.assertGreaterEqual(plan["next_time"], due_at)
        self.assertIn("野外历练", plan["reason"])

    def test_craft_farm_yields_to_wild_training_inside_prediction_lock(self):
        now = 1_780_000_000.0
        due_at = now + 2 * 3600
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=12)
            state_module.state["wild_training_enabled"] = True
            state_module.state["next_wild_training_time"] = due_at
            state_module.state["tianxing_observation"]["current_prediction"] = "探索"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 8 * 3600
            plan = tianxing.build_tianxing_craft_farm_plan(
                now=now,
                config=self._active_config(
                    now,
                    timeline_enabled=True,
                    farm_route="炼制",
                    craft_farm_enabled=True,
                    craft_farm_dry_run_enabled=False,
                    craft_farm_item="玄铁剑",
                    craft_farm_daily_limit=42,
                    route_prepare_lead_sec=300,
                    min_tianji_for_change=6,
                ),
            )

        self.assertEqual("waiting_consume_window", plan["stage"])
        self.assertEqual("", plan["command"])
        self.assertIn("推命锁定期", plan["reason"])

    def test_craft_farm_does_not_yield_to_consumed_explore_prediction_lock(self):
        now = 1_780_000_000.0
        due_at = now + 2 * 3600
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=31)
            state_module.state["wild_training_enabled"] = True
            state_module.state["next_wild_training_time"] = due_at
            state_module.state["tianxing_observation"]["current_change"] = "探索"
            state_module.state["tianxing_observation"]["current_change_until"] = now + 12 * 3600
            plan = tianxing.build_tianxing_craft_farm_plan(
                now=now,
                config=self._active_config(
                    now,
                    timeline_enabled=True,
                    farm_route="炼制",
                    craft_farm_enabled=True,
                    craft_farm_dry_run_enabled=False,
                    craft_farm_item="玄铁剑",
                    craft_farm_daily_limit=42,
                    craft_farm_off_window_enabled=True,
                    route_prepare_lead_sec=300,
                    min_tianji_for_change=6,
                ),
            )

        self.assertNotEqual("waiting_consume_window", plan["stage"])

    def test_craft_farm_yields_to_overdue_explore_rift_consume_window(self):
        now = 1_780_000_000.0
        due_at = now - 20 * 60
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=12)
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = due_at
            state_module.state["tianxing_observation"]["current_change"] = "探索"
            state_module.state["tianxing_observation"]["current_change_until"] = now + 12 * 3600
            plan = tianxing.build_tianxing_craft_farm_plan(
                now=now,
                config=self._active_config(
                    now,
                    timeline_enabled=True,
                    farm_route="炼制",
                    craft_farm_enabled=True,
                    craft_farm_dry_run_enabled=False,
                    craft_farm_item="玄铁剑",
                    craft_farm_daily_limit=42,
                    route_prepare_lead_sec=300,
                ),
            )

        self.assertEqual("waiting_consume_window", plan["stage"])
        self.assertEqual("", plan["command"])
        self.assertIn("探寻裂缝", plan["reason"])

    def test_craft_farm_keeps_catching_up_when_explore_change_lacks_tianji(self):
        now = 1_780_000_000.0
        due_at = now + 240
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=2)
            state_module.state["explore_rift_enabled"] = True
            state_module.state["next_explore_rift_time"] = due_at
            plan = tianxing.build_tianxing_craft_farm_plan(
                now=now,
                config=self._active_config(
                    now,
                    timeline_enabled=True,
                    farm_route="炼制",
                    craft_farm_enabled=True,
                    craft_farm_dry_run_enabled=False,
                    craft_farm_item="玄铁剑",
                    craft_farm_daily_limit=42,
                    route_prepare_lead_sec=300,
                    min_tianji_for_change=6,
                ),
            )

        self.assertNotEqual("waiting_consume_window", plan["stage"])
        self.assertEqual("timeline_required", plan["stage"])
        self.assertTrue(plan["timeline_required"])

    def test_craft_farm_risks_unpredicted_craft_when_tianji_short(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=2)
            state_module.state["tianxing_observation"]["current_prediction"] = "探索"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 3600
            state_module.state["tianxing_timeline_state"] = {
                "phase": "prediction_conflict",
                "route": "炼制",
                "blocked_until": now + 3600,
                "last_error": "已有探索推命尚未应验，炼制攒点等待。",
                "craft_farm": {
                    "phase": "prediction_conflict",
                    "next_time": now + 3600,
                    "estimated_tianji": 2,
                },
            }
            plan = tianxing.build_tianxing_craft_farm_plan(
                now=now,
                config=self._active_config(
                    now,
                    timeline_enabled=True,
                    farm_route="炼制",
                    craft_farm_enabled=True,
                    craft_farm_dry_run_enabled=False,
                    craft_farm_item="玄铁剑",
                    craft_farm_daily_limit=42,
                    min_tianji_for_change=3,
                ),
            )

        self.assertEqual("send_craft_unpredicted", plan["stage"])
        self.assertEqual(".炼制 玄铁剑", plan["command"])
        self.assertTrue(plan["allow_prediction_conflict"])
        self.assertIn("低于改命阈值", plan["reason"])

    def test_craft_farm_waits_on_conflicting_prediction_when_tianji_enough(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=12)
            state_module.state["tianxing_observation"]["current_prediction"] = "探索"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 3600
            state_module.state["tianxing_timeline_state"] = {
                "phase": "prediction_conflict",
                "route": "炼制",
                "blocked_until": now + 3600,
                "last_error": "已有探索推命尚未应验，炼制攒点等待。",
            }
            plan = tianxing.build_tianxing_craft_farm_plan(
                now=now,
                config=self._active_config(
                    now,
                    timeline_enabled=True,
                    farm_route="炼制",
                    craft_farm_enabled=True,
                    craft_farm_dry_run_enabled=False,
                    craft_farm_item="玄铁剑",
                    craft_farm_daily_limit=42,
                    min_tianji_for_change=3,
                ),
            )

        self.assertEqual("waiting_prediction_conflict", plan["stage"])
        self.assertEqual("", plan["command"])

    def test_craft_farm_is_not_blocked_by_existing_explore_change_fate(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=12)
            state_module.state["tianxing_observation"]["current_change"] = "探索"
            state_module.state["tianxing_observation"]["current_change_until"] = now + 12 * 3600
            plan = tianxing.build_tianxing_craft_farm_plan(
                now=now,
                config=self._active_config(
                    now,
                    timeline_enabled=True,
                    farm_route="炼制",
                    craft_farm_enabled=True,
                    craft_farm_dry_run_enabled=False,
                    craft_farm_item="玄铁剑",
                    craft_farm_daily_limit=42,
                ),
            )

        self.assertEqual("timeline_required", plan["stage"])
        self.assertTrue(plan["active"])
        self.assertEqual("timeline", plan["action"])
        self.assertIn("等待天星时间线确认", plan["reason"])

    def test_craft_farm_respects_manual_pause(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=12)
            state_module.state["tianxing_observation"].update({
                "current_prediction": "炼制",
                "current_prediction_until": now + 3600,
                "automation_paused_until": -1,
                "automation_paused_reason": "手动测试",
            })
            plan = tianxing.build_tianxing_craft_farm_plan(
                now=now,
                config=self._active_config(
                    now,
                    farm_route="炼制",
                    craft_farm_enabled=True,
                    craft_farm_dry_run_enabled=False,
                    craft_farm_item="玄铁剑",
                    craft_farm_daily_limit=42,
                ),
            )

        self.assertEqual("automation_paused", plan["stage"])
        self.assertTrue(plan["active"])
        self.assertFalse(plan["takeover"])
        self.assertIn("已暂停", plan["reason"])

    def test_retreat_farm_calibration_wait_is_not_treated_as_retreat_cd(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now)
            state_module.state["tianxing_timeline_state"] = {
                "retreat_farm": {
                    "phase": "calibrating",
                    "started_at": now - 600,
                    "next_time": now + 60,
                    "target_tianji": 42,
                }
            }
            waiting = tianxing.build_tianxing_retreat_farm_plan(
                now=now,
                config=self._active_config(now),
            )
            due = tianxing.build_tianxing_retreat_farm_plan(
                now=now + 61,
                config=self._active_config(now + 61),
            )

        self.assertEqual("waiting_calibration", waiting["stage"])
        self.assertEqual("", waiting["command"])
        self.assertEqual("calibrate_panel", due["stage"])
        self.assertEqual(".天机盘", due["command"])

    async def test_retreat_farm_sends_normal_retreat_after_preflight_release(self):
        now = 1_780_000_000.0
        sent_msg = SimpleNamespace(id=9301, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now)
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(tianxing, "save_state"),
            ):
                result = await tianxing.run_tianxing_retreat_farm_scheduler(
                    now,
                    config=self._active_config(now),
                )
            farm = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["retreat_farm"]

        self.assertEqual("sent_waiting_reply", result["stage"])
        send_mock.assert_awaited_once()
        self.assertEqual(".闭关修炼", send_mock.await_args.args[0])
        self.assertEqual("天星宗", send_mock.await_args.kwargs["source_module"])
        self.assertEqual("normal", send_mock.await_args.kwargs["priority"])
        self.assertEqual("sent_waiting_reply", farm["phase"])
        self.assertEqual(9301, farm["last_msg_id"])

    async def test_retreat_farm_swallowed_reply_calibrates_panel_without_resending_retreat(self):
        now = 1_780_000_000.0
        sent_msg = SimpleNamespace(id=9303, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now)
            state_module.state["tianxing_timeline_state"] = {
                "retreat_farm": {
                    "phase": "sent_waiting_reply",
                    "started_at": now - 300,
                    "next_time": now - 1,
                    "target_tianji": 42,
                    "last_command": ".闭关修炼",
                }
            }
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(tianxing, "save_state"),
            ):
                result = await tianxing.run_tianxing_retreat_farm_scheduler(
                    now,
                    config=self._active_config(now),
                )
            farm = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["retreat_farm"]

        self.assertEqual("sent_waiting_reply", result["stage"])
        send_mock.assert_awaited_once()
        self.assertEqual(".天机盘", send_mock.await_args.args[0])
        self.assertNotEqual(".闭关修炼", send_mock.await_args.args[0])
        self.assertEqual(".天机盘", farm["last_command"])

    def test_retreat_farm_late_retreat_reply_updates_calibration_instead_of_resend(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now)
            state_module.state["tianxing_timeline_state"] = {
                "retreat_farm": {
                    "phase": "sent_waiting_reply",
                    "started_at": now - 300,
                    "next_time": now - 1,
                    "target_tianji": 42,
                    "last_command": ".闭关修炼",
                }
            }
            changed = tianxing.apply_tianxing_passive(
                real_text("tianxing.retreat.success"),
                now=now,
                family="tianxing_retreat_farm",
            )
            farm = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["retreat_farm"]

        self.assertTrue(changed)
        self.assertEqual("calibrating", farm["phase"])
        self.assertEqual(1, farm["last_tianji_gain"])
        self.assertEqual(now + tianxing.TIANXING_RETREAT_FARM_CALIBRATION_DELAY_SEC, farm["next_time"])

    async def test_retreat_farm_uses_heqi_dan_only_when_config_allows(self):
        now = 1_780_000_000.0
        sent_msg = SimpleNamespace(id=9302, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now)
            state_module.state["tianxing_timeline_state"] = {
                "retreat_farm": {
                    "phase": "cooldown",
                    "started_at": now - 600,
                    "next_time": now + 600,
                    "target_tianji": 42,
                }
            }
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(tianxing, "save_state"),
            ):
                result = await tianxing.run_tianxing_retreat_farm_scheduler(
                    now,
                    config=self._active_config(now, retreat_farm_allow_heqi_dan=True),
                )

        self.assertEqual("sent_waiting_reply", result["stage"])
        send_mock.assert_awaited_once()
        self.assertEqual(".服用 合气丹", send_mock.await_args.args[0])
        self.assertEqual("chain", send_mock.await_args.kwargs["priority"])

    def test_retreat_farm_heqi_reply_sets_ready_and_unknown_reply_does_not_advance(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now)
            state_module.state["tianxing_timeline_state"] = {
                "retreat_farm": {
                    "phase": "sent_waiting_reply",
                    "started_at": now - 30,
                    "next_time": now + 60,
                    "target_tianji": 42,
                    "last_command": ".服用 合气丹",
                }
            }
            unknown = tianxing.apply_tianxing_passive(
                "司命盘忽然震颤，似有星辉流转，但没有明确的推命或改命结果。",
                now=now,
                family="tianxing_panel",
            )
            farm_after_unknown = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["retreat_farm"]

            heqi = tianxing.apply_tianxing_passive(
                real_text("tianxing.retreat_farm.heqi_dan_success"),
                now=now + 1,
                family="tianxing_retreat_farm",
            )
            farm_after_heqi = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["retreat_farm"]

        self.assertTrue(unknown)
        self.assertEqual("sent_waiting_reply", farm_after_unknown["phase"])
        self.assertTrue(heqi)
        self.assertEqual("ready", farm_after_heqi["phase"])
        self.assertEqual(now + 1, farm_after_heqi["next_time"])

    async def test_retreat_farm_missing_heqi_exchanges_then_uses_it(self):
        now = 1_780_000_000.0
        config = self._active_config(
            now,
            retreat_farm_allow_heqi_dan=True,
            retreat_farm_auto_exchange_heqi_dan=True,
            retreat_farm_heqi_exchange_count=10,
        )
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now)
            state_module.state["tianxing_auto_config"] = config
            state_module.state["tianxing_timeline_state"] = {
                "retreat_farm": {
                    "phase": "sent_waiting_reply",
                    "started_at": now - 30,
                    "next_time": now + 60,
                    "cooldown_until": now + 600,
                    "target_tianji": 42,
                    "last_command": ".服用 合气丹",
                }
            }

            tianxing.apply_tianxing_passive(
                "你的储物袋中没有名为【合气丹】的可用物品。",
                now=now,
                family="tianxing_retreat_farm",
            )
            farm_after_missing = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["retreat_farm"]

            exchange_msg = SimpleNamespace(id=9304, sent_at=now + 1)
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock(return_value=exchange_msg)) as exchange_mock,
                patch.object(tianxing, "save_state"),
            ):
                exchange_result = await tianxing.run_tianxing_retreat_farm_scheduler(now + 1, config=config)

            tianxing.apply_tianxing_passive(
                "兑换成功！\n你消耗了 1500 点贡献，获得了【合气丹】x10，已放入你的储物袋。",
                now=now + 2,
                family="tianxing_retreat_farm",
            )
            farm_after_exchange = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["retreat_farm"]

            use_msg = SimpleNamespace(id=9305, sent_at=now + 3)
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock(return_value=use_msg)) as use_mock,
                patch.object(tianxing, "save_state"),
            ):
                use_result = await tianxing.run_tianxing_retreat_farm_scheduler(now + 3, config=config)

        self.assertEqual("need_heqi_exchange", farm_after_missing["phase"])
        self.assertEqual(now + 600, farm_after_missing["cooldown_until"])
        self.assertEqual("sent_waiting_reply", exchange_result["stage"])
        self.assertEqual(".兑换 合气丹*10", exchange_mock.await_args.args[0])
        self.assertEqual("chain", exchange_mock.await_args.kwargs["priority"])
        self.assertEqual("ready_to_use_heqi", farm_after_exchange["phase"])
        self.assertEqual("sent_waiting_reply", use_result["stage"])
        self.assertEqual(".服用 合气丹", use_mock.await_args.args[0])

    async def test_retreat_farm_exchange_shortage_donates_then_retries_exchange(self):
        now = 1_780_000_000.0
        config = self._active_config(
            now,
            retreat_farm_allow_heqi_dan=True,
            retreat_farm_auto_exchange_heqi_dan=True,
            retreat_farm_heqi_exchange_count=10,
            retreat_farm_auto_donate_lingshi=True,
            retreat_farm_donate_lingshi_count=200,
        )
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now)
            state_module.state["tianxing_auto_config"] = config
            state_module.state["tianxing_timeline_state"] = {
                "retreat_farm": {
                    "phase": "sent_waiting_reply",
                    "started_at": now - 30,
                    "next_time": now + 60,
                    "cooldown_until": now + 600,
                    "target_tianji": 42,
                    "last_command": ".兑换 合气丹*10",
                }
            }

            tianxing.apply_tianxing_passive(
                "你的宗门贡献不足！\n兑换【合气丹】x10 需要 1500 点贡献，你只有 270 点。",
                now=now,
                family="tianxing_retreat_farm",
            )
            donate_msg = SimpleNamespace(id=9306, sent_at=now + 1)
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock(return_value=donate_msg)) as donate_mock,
                patch.object(tianxing, "save_state"),
            ):
                donate_result = await tianxing.run_tianxing_retreat_farm_scheduler(now + 1, config=config)

            tianxing.apply_tianxing_passive(
                "你向宗门捐献了 【灵石】x200，获得了 1400 点宗门贡献！",
                now=now + 2,
                family="tianxing_retreat_farm",
            )
            farm_after_donation = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["retreat_farm"]

            exchange_msg = SimpleNamespace(id=9307, sent_at=now + 3)
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock(return_value=exchange_msg)) as exchange_mock,
                patch.object(tianxing, "save_state"),
            ):
                exchange_result = await tianxing.run_tianxing_retreat_farm_scheduler(now + 3, config=config)

        self.assertEqual("sent_waiting_reply", donate_result["stage"])
        self.assertEqual(".宗门捐献 灵石*200", donate_mock.await_args.args[0])
        self.assertEqual("chain", donate_mock.await_args.kwargs["priority"])
        self.assertEqual("need_heqi_exchange", farm_after_donation["phase"])
        self.assertEqual("sent_waiting_reply", exchange_result["stage"])
        self.assertEqual(".兑换 合气丹*10", exchange_mock.await_args.args[0])

    def test_retreat_farm_force_exit_is_dry_run_by_default(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now)
            result = tianxing.build_tianxing_retreat_farm_plan(
                now=now,
                deep_retreat_phase="running",
                config=self._active_config(
                    now,
                    retreat_farm_dry_run_enabled=True,
                    retreat_farm_allow_force_exit=True,
                ),
            )

        self.assertEqual("force_exit_deep_retreat", result["stage"])
        self.assertEqual(".强行出关", result["command"])
        self.assertFalse(result["takeover"])
        self.assertTrue(result["dry_run"])

    def test_force_exit_summary_records_normal_retreat_cooldown(self):
        now = 1_780_000_000.0
        text = (
            "【深度闭关总结】\n"
            "【强行出关惩罚】: 因你强行中断修行，所得感悟流失大半。\n"
            "你的神魂因中断修行而震荡不休，需调息40分钟方可进行下一次【闭关修炼】。"
        )
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now)
            state_module.state["tianxing_timeline_state"] = {
                "retreat_farm": {
                    "phase": "sent_waiting_reply",
                    "started_at": now - 30,
                    "last_action": "force_exit",
                    "last_command": ".强行出关",
                    "target_tianji": 42,
                }
            }
            changed = tianxing.note_tianxing_retreat_force_exit_summary(text, now=now)
            farm = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["retreat_farm"]

        self.assertTrue(changed)
        self.assertEqual("cooldown", farm["phase"])
        self.assertEqual("强行出关后普通闭关调息中", farm["last_result"])
        self.assertGreaterEqual(farm["next_time"], now + 40 * 60)

    async def test_craft_farm_sends_craft_after_timeline_release(self):
        now = 1_780_000_000.0
        sent_msg = SimpleNamespace(id=9401, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=3)
            state_module.state["tianxing_observation"]["current_prediction"] = "炼制"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 3600
            state_module.state["tianxing_timeline_state"] = {
                "released_routes": {
                    "炼制": {"released_at": now, "plan_id": "test", "reason": "炼制已放行", "basis": "prediction"}
                }
            }
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(tianxing, "save_state"),
            ):
                result = await tianxing.run_tianxing_craft_farm_scheduler(
                    now,
                    config=self._active_config(
                        now,
                        farm_route="炼制",
                        craft_farm_enabled=True,
                        craft_farm_dry_run_enabled=False,
                        craft_farm_item="玄铁剑",
                        craft_farm_daily_limit=42,
                    ),
                )
            craft = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["craft_farm"]

        self.assertEqual("sent_waiting_reply", result["stage"])
        send_mock.assert_awaited_once()
        self.assertEqual(".炼制 玄铁剑", send_mock.await_args.args[0])
        self.assertEqual("天星宗", send_mock.await_args.kwargs["source_module"])
        self.assertEqual("sent_waiting_reply", craft["phase"])
        self.assertEqual("玄铁剑", craft["last_item"])

    async def test_consume_craft_prediction_sends_craft_outside_farm_window(self):
        now = 1_780_000_000.0
        sent_msg = SimpleNamespace(id=9409, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=32)
            state_module.state["tianxing_observation"]["current_prediction"] = "炼制"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 3600
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(tianxing, "save_state"),
            ):
                result = await tianxing.run_tianxing_consume_craft_prediction(
                    now,
                    reason="野外历练前消费炼制推命",
                    config=self._active_config(
                        now,
                        farm_route="炼制",
                        craft_farm_enabled=True,
                        craft_farm_dry_run_enabled=False,
                        craft_farm_item="玄铁剑",
                        farm_window_start="00:00",
                        farm_window_duration_min=1,
                    ),
                )
            craft = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["craft_farm"]

        self.assertEqual("sent_waiting_reply", result["stage"])
        self.assertEqual("consume_craft_prediction", result["action"])
        send_mock.assert_awaited_once()
        self.assertEqual(".炼制 玄铁剑", send_mock.await_args.args[0])
        self.assertEqual("天星宗", send_mock.await_args.kwargs["source_module"])
        self.assertEqual("sent_waiting_reply", craft["phase"])
        self.assertEqual("consume_craft_prediction", craft["last_action"])
        self.assertEqual(9409, craft["last_msg_id"])

    async def test_craft_farm_sends_unpredicted_craft_when_tianji_short(self):
        now = 1_780_000_000.0
        sent_msg = SimpleNamespace(id=9402, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=2)
            state_module.state["tianxing_observation"]["current_prediction"] = "探索"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 3600
            state_module.state["tianxing_timeline_state"] = {
                "phase": "prediction_conflict",
                "route": "炼制",
                "blocked_until": now + 3600,
                "last_error": "已有探索推命尚未应验，炼制攒点等待。",
            }
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(tianxing, "save_state"),
            ):
                result = await tianxing.run_tianxing_craft_farm_scheduler(
                    now,
                    config=self._active_config(
                        now,
                        timeline_enabled=True,
                        farm_route="炼制",
                        craft_farm_enabled=True,
                        craft_farm_dry_run_enabled=False,
                        craft_farm_item="玄铁剑",
                        craft_farm_daily_limit=42,
                        min_tianji_for_change=3,
                    ),
                )
            craft = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["craft_farm"]

        self.assertEqual("sent_waiting_reply", result["stage"])
        send_mock.assert_awaited_once()
        self.assertEqual(".炼制 玄铁剑", send_mock.await_args.args[0])
        self.assertEqual("sent_waiting_reply", craft["phase"])
        self.assertEqual("玄铁剑", craft["last_item"])

    async def test_craft_farm_does_not_reuse_release_after_prediction_is_consumed(self):
        now = 1_780_000_000.0
        hit_text = (
            "炼制结束！\n"
            "共开炉 1 次，成功 1 次。\n"
            "最终获得【玄铁剑】x1！\n"
            "【推命命中】司命演算吻合，天机值 +1，宗门贡献 +30"
        )
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=3)
            state_module.state["tianxing_auto_config"] = self._active_config(
                now,
                timeline_enabled=True,
                farm_route="炼制",
                craft_farm_enabled=True,
                craft_farm_dry_run_enabled=False,
                craft_farm_item="玄铁剑",
                craft_farm_daily_limit=42,
            )
            state_module.state["tianxing_observation"]["current_prediction"] = "炼制"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 3600
            state_module.state["tianxing_timeline_state"] = {
                "phase": "downstream_released",
                "released_routes": {
                    "炼制": {"released_at": now - 10, "plan_id": "test", "reason": "炼制已放行", "basis": "prediction"}
                },
                "craft_farm": {
                    "phase": "sent_waiting_reply",
                    "started_at": now - 20,
                    "target_tianji": 42,
                    "daily_limit": 42,
                    "last_command": ".炼制 玄铁剑",
                },
            }
            self.assertTrue(tianxing.is_tianxing_route_released("炼制", now=now - 1))
            tianxing.apply_tianxing_passive(hit_text, now=now, family="tianxing_craft_farm")
            self.assertFalse(tianxing.is_tianxing_route_released("炼制", now=now + 1))

            with (
                patch.object(tianxing, "run_tianxing_timeline_scheduler", new=AsyncMock(return_value={"phase": "sent_waiting_ack", "changed": True})) as timeline_mock,
                patch.object(tianxing, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(tianxing, "save_state"),
            ):
                result = await tianxing.run_tianxing_craft_farm_scheduler(
                    now + 21,
                    config=state_module.state["tianxing_auto_config"],
                )

        self.assertEqual("timeline_required", result["stage"])
        self.assertGreaterEqual(timeline_mock.await_count, 1)
        send_mock.assert_not_awaited()

    async def test_craft_farm_waiting_reply_preserves_pending_phase_without_resend(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=3)
            state_module.state["tianxing_observation"]["current_prediction"] = "炼制"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 3600
            state_module.state["tianxing_timeline_state"] = {
                "released_routes": {
                    "炼制": {"released_at": now - 30, "plan_id": "test", "reason": "炼制已放行", "basis": "prediction"}
                },
                "craft_farm": {
                    "phase": "sent_waiting_reply",
                    "started_at": now - 60,
                    "next_time": now + 120,
                    "target_tianji": 42,
                    "daily_limit": 42,
                    "last_command": ".炼制 玄铁剑",
                    "last_msg_id": 9401,
                },
            }
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(tianxing, "save_state"),
            ):
                result = await tianxing.run_tianxing_craft_farm_scheduler(
                    now,
                    config=self._active_config(
                        now,
                        farm_route="炼制",
                        craft_farm_enabled=True,
                        craft_farm_dry_run_enabled=False,
                        craft_farm_item="玄铁剑",
                        craft_farm_daily_limit=42,
                    ),
                )
            craft = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["craft_farm"]

        self.assertEqual("waiting_reply", result["stage"])
        send_mock.assert_not_awaited()
        self.assertEqual("sent_waiting_reply", craft["phase"])
        self.assertEqual(now + 120, craft["next_time"])

    async def test_craft_farm_preparing_waits_for_final_without_resend(self):
        now = 1_780_000_000.0
        preparing_text = "准备同时开炼 1 炉【玄铁剑】..."
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=3)
            state_module.state["tianxing_auto_config"] = self._active_config(
                now,
                farm_route="炼制",
                craft_farm_enabled=True,
                craft_farm_dry_run_enabled=False,
                craft_farm_item="玄铁剑",
                craft_farm_daily_limit=42,
            )
            state_module.state["tianxing_observation"]["current_prediction"] = "炼制"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 3600
            state_module.state["tianxing_timeline_state"] = {
                "released_routes": {
                    "炼制": {"released_at": now - 30, "plan_id": "test", "reason": "炼制已放行", "basis": "prediction"}
                },
                "craft_farm": {
                    "phase": "sent_waiting_reply",
                    "started_at": now - 60,
                    "next_time": now + 120,
                    "target_tianji": 42,
                    "daily_limit": 42,
                    "last_command": ".炼制 玄铁剑",
                    "last_msg_id": 9401,
                },
            }
            self.assertTrue(tianxing.apply_tianxing_passive(preparing_text, now=now, family="tianxing_craft_farm"))
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(tianxing, "save_state"),
            ):
                result = await tianxing.run_tianxing_craft_farm_scheduler(
                    now + 30,
                    config=state_module.state["tianxing_auto_config"],
                )
            craft = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["craft_farm"]

        self.assertEqual("waiting_reply", result["stage"])
        send_mock.assert_not_awaited()
        self.assertEqual("crafting_waiting_final", craft["phase"])
        self.assertGreater(craft["next_time"], now + 30)

    async def test_craft_farm_plain_success_with_active_prediction_calibrates_before_next_craft(self):
        now = 1_780_000_000.0
        success_text = (
            "炼制结束！\n"
            "共开炉 1 次，成功 1 次。\n"
            "最终获得【玄铁剑】x1！"
        )
        panel_msg = SimpleNamespace(id=9402, sent_at=now + 61)
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=3)
            state_module.state["tianxing_auto_config"] = self._active_config(
                now,
                farm_route="炼制",
                craft_farm_enabled=True,
                craft_farm_dry_run_enabled=False,
                craft_farm_item="玄铁剑",
                craft_farm_daily_limit=42,
            )
            state_module.state["tianxing_observation"]["current_prediction"] = "炼制"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 3600
            state_module.state["tianxing_timeline_state"] = {
                "released_routes": {
                    "炼制": {"released_at": now - 30, "plan_id": "test", "reason": "炼制已放行", "basis": "prediction"}
                },
                "craft_farm": {
                    "phase": "crafting_waiting_final",
                    "started_at": now - 60,
                    "next_time": now + 120,
                    "target_tianji": 42,
                    "daily_limit": 42,
                    "last_command": ".炼制 玄铁剑",
                    "last_msg_id": 9401,
                },
            }
            self.assertTrue(tianxing.apply_tianxing_passive(success_text, now=now, family="tianxing_craft_farm"))
            craft_after_success = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["craft_farm"]
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock(return_value=panel_msg)) as send_mock,
                patch.object(tianxing, "save_state"),
            ):
                result = await tianxing.run_tianxing_craft_farm_scheduler(
                    now + tianxing.TIANXING_CRAFT_FARM_CALIBRATION_DELAY_SEC + 1,
                    config=state_module.state["tianxing_auto_config"],
                )

        self.assertEqual("calibrating", craft_after_success["phase"])
        self.assertEqual(0, craft_after_success["daily_count"])
        self.assertEqual(0, craft_after_success["success_count"])
        self.assertEqual(0, craft_after_success["hit_count"])
        self.assertEqual(0, craft_after_success["last_tianji_gain"])
        self.assertIn("查盘校准", craft_after_success["last_error"])
        self.assertEqual("sent_waiting_reply", result["stage"])
        send_mock.assert_awaited_once()
        self.assertEqual(".天机盘", send_mock.await_args.args[0])

    async def test_craft_farm_preserves_prediction_conflict_from_timeline_race(self):
        now = 1_780_000_000.0

        async def mark_conflict(*_args, **_kwargs):
            state_module.state["tianxing_timeline_state"] = {
                "phase": "prediction_conflict",
                "route": "炼制",
                "blocked_until": now + 3600,
                "last_error": "已有 闭关 推命尚未应验，不能切到 炼制；等待当前推命消费或过期。",
                "craft_farm": {
                    "phase": "timeline_waiting",
                    "started_at": now - 5,
                    "target_tianji": 42,
                    "daily_limit": 42,
                    "estimated_tianji": 3,
                },
            }
            return {"phase": "prediction_conflict", "changed": True, "reason": state_module.state["tianxing_timeline_state"]["last_error"]}

        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=3)
            with (
                patch.object(tianxing, "run_tianxing_timeline_scheduler", new=AsyncMock(side_effect=mark_conflict)) as timeline_mock,
                patch.object(tianxing, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(tianxing, "save_state"),
            ):
                result = await tianxing.run_tianxing_craft_farm_scheduler(
                    now,
                    config=self._active_config(
                        now,
                        timeline_enabled=True,
                        farm_route="炼制",
                        craft_farm_enabled=True,
                        craft_farm_dry_run_enabled=False,
                        craft_farm_item="玄铁剑",
                        craft_farm_daily_limit=42,
                    ),
                )
            craft = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["craft_farm"]

        timeline_mock.assert_awaited_once()
        send_mock.assert_not_awaited()
        self.assertEqual("timeline_required", result["stage"])
        self.assertEqual("prediction_conflict", result["timeline_phase"])
        self.assertEqual("prediction_conflict", craft["phase"])
        self.assertEqual(now + 3600, craft["next_time"])

    async def test_craft_farm_consumes_conflicting_retreat_prediction_when_configured(self):
        now = 1_780_000_000.0
        sent_msg = SimpleNamespace(id=9501, sent_at=now)
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=3)
            state_module.state["deep_retreat_phase"] = "running"
            state_module.state["tianxing_observation"]["current_prediction"] = "闭关"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 3600
            state_module.state["tianxing_timeline_state"] = {
                "phase": "prediction_conflict",
                "route": "炼制",
                "blocked_until": now + 3600,
                "last_error": "已有 闭关 推命尚未应验，不能切到 炼制。",
                "released_routes": {
                    "闭关": {"released_at": now - 5, "plan_id": "test", "reason": "consume"}
                },
            }
            with (
                patch.object(tianxing, "send_game_command", new=AsyncMock(return_value=sent_msg)) as send_mock,
                patch.object(tianxing, "save_state"),
            ):
                result = await tianxing.run_tianxing_craft_farm_scheduler(
                    now,
                    config=self._active_config(
                        now,
                        timeline_enabled=True,
                        farm_route="炼制",
                        craft_farm_enabled=True,
                        craft_farm_dry_run_enabled=False,
                        consume_conflicting_prediction_enabled=True,
                        retreat_farm_allow_force_exit=True,
                        retreat_farm_allow_heqi_dan=True,
                    ),
                )
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])

        send_mock.assert_awaited_once()
        self.assertEqual(".强行出关", send_mock.await_args.args[0])
        self.assertEqual("深度闭关", send_mock.await_args.kwargs["source_module"])
        self.assertEqual("chain", send_mock.await_args.kwargs["priority"])
        self.assertEqual("consume_conflicting_prediction", result["stage"])
        self.assertEqual("sent_waiting_reply", result["consume_stage"])
        self.assertEqual(".强行出关", result["consume_command"])
        self.assertEqual("consume_prediction", timeline["craft_farm"]["phase"])
        self.assertEqual("sent_waiting_reply", timeline["retreat_farm"]["phase"])

    def test_craft_farm_ignores_stale_prediction_conflict_after_prediction_consumed(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=6)
            state_module.state["tianxing_observation"]["current_prediction"] = ""
            state_module.state["tianxing_observation"]["current_prediction_until"] = 0
            state_module.state["tianxing_timeline_state"] = {
                "phase": "prediction_conflict",
                "route": "炼制",
                "blocked_until": now + 3600,
                "last_error": "旧阻断",
            }
            plan = tianxing.build_tianxing_craft_farm_plan(
                now=now,
                config=self._active_config(
                    now,
                    timeline_enabled=True,
                    farm_route="炼制",
                    craft_farm_enabled=True,
                    craft_farm_dry_run_enabled=False,
                    consume_conflicting_prediction_enabled=True,
                ),
            )

        self.assertEqual("timeline_required", plan["stage"])
        self.assertNotEqual("waiting_prediction_conflict", plan["stage"])

    def test_craft_farm_reply_accounts_hit_once_and_estimates_tianji(self):
        now = 1_780_000_000.0
        text = (
            "炼制结束！\n"
            "共开炉 1 次，成功 1 次。\n"
            "最终获得【玄铁剑】x1！\n\n"
            "命盘【天府】照命，主丹器之成与稳守之势，炼制更稳，斗法更耐打，偶有额外成品。\n"
            "【推命命中】司命演算吻合，天机值 +1，宗门贡献 +30"
        )
        with state_module.use_identity(self.identity_id):
            self._prepare_identity(now, tianji_value=3)
            state_module.state["tianxing_auto_config"] = self._active_config(
                now,
                farm_route="炼制",
                craft_farm_enabled=True,
                craft_farm_dry_run_enabled=False,
                craft_farm_daily_limit=42,
            )
            state_module.state["tianxing_observation"]["current_prediction"] = "炼制"
            state_module.state["tianxing_observation"]["current_prediction_until"] = now + 3600
            state_module.state["tianxing_timeline_state"] = {
                "craft_farm": {
                    "phase": "sent_waiting_reply",
                    "started_at": now - 10,
                    "target_tianji": 42,
                    "daily_limit": 42,
                    "last_command": ".炼制 玄铁剑",
                }
            }
            changed = tianxing.apply_tianxing_passive(text, now=now, family="tianxing_craft_farm")
            observed = state_module.state["tianxing_observation"]
            craft = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])["craft_farm"]

        self.assertTrue(changed)
        self.assertEqual(4, observed["tianji_value"])
        self.assertEqual("", observed["current_prediction"])
        self.assertEqual(1, craft["daily_count"])
        self.assertEqual(1, craft["hit_count"])
        self.assertEqual(4, craft["estimated_tianji"])
        self.assertEqual("ready", craft["phase"])


class TianxingSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        self.identity_id = 2102
        state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(
            self.identity_id,
            username="tianxing_auto",
            label="tianxing_auto",
            sect_name="天星宗",
        )

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    async def _run_with_observation(self, observation, now=1_780_000_000.0, config=None):
        msg = SimpleNamespace(id=9101, sent_at=now)
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = observation
            state_module.state["tianxing_auto_config"] = config or {}
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command", return_value=msg) as send_mock:
                await tianxing.run_tianxing_scheduler(now)
            return send_mock, state_module.state["tianxing_observation"]

    async def test_scheduler_queries_panel_when_observation_missing_or_stale(self):
        send_mock, observed = await self._run_with_observation({})

        send_mock.assert_awaited_once()
        self.assertEqual(".观命", send_mock.await_args.args[0])
        self.assertEqual("天星宗", send_mock.await_args.kwargs["source_module"])
        self.assertEqual("observe", observed["auto_last_action"])
        self.assertEqual("observe", observed["auto_pending_action"])
        self.assertEqual(".观命", observed["auto_pending_command"])
        self.assertEqual(9101, observed["auto_pending_msg_id"])

    async def test_scheduler_bypasses_future_auto_time_for_daily_observe(self):
        now = local_ts(0, 1, year=2026, month=6, day=30)
        observation = {
            "last_observed_at": now - 60,
            "available_stars": ["太阴", "贪狼"],
            "available_stars_day": tianxing.get_day_key(now - 3600),
            "fixed_star": "太阴",
            "fixed_star_day": tianxing.get_day_key(now - 3600),
            "auto_next_time": now + 6 * 3600,
        }

        send_mock, observed = await self._run_with_observation(observation, now=now, config={
            "auto_observe_enabled": True,
            "daily_observe_enabled": True,
        })

        send_mock.assert_awaited_once()
        self.assertEqual(".观命", send_mock.await_args.args[0])
        self.assertEqual("observe", observed["auto_last_action"])

    async def test_scheduler_bypasses_future_auto_time_for_daily_set_star(self):
        now = local_ts(0, 2, year=2026, month=6, day=30)
        observation = {
            "last_observed_at": now - 30,
            "available_stars": ["紫微", "贪狼", "天府"],
            "available_stars_source": "observe",
            "available_stars_day": tianxing.get_day_key(now),
            "fixed_star": "",
            "fixed_star_day": "",
            "current_prediction": "",
            "current_prediction_until": 0,
            "calamity_count": 0,
            "tianji_value": 36,
            "auto_next_time": now + 6 * 3600,
        }

        send_mock, observed = await self._run_with_observation(observation, now=now, config={
            "timeline_enabled": True,
            "auto_set_star_enabled": True,
            "daily_set_star_enabled": True,
            "strategy_dry_run_enabled": False,
            "star_priority": ["太阴", "贪狼", "天府", "紫微"],
        })

        send_mock.assert_awaited_once()
        self.assertEqual(".定命 贪狼", send_mock.await_args.args[0])
        self.assertEqual("set_star", observed["auto_last_action"])

    async def test_scheduler_drains_pending_release_even_outside_farm_window(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 10,
                "available_stars": ["贪狼", "太阴"],
                "available_stars_day": tianxing.get_day_key(now),
                "fixed_star": "贪狼",
                "fixed_star_day": tianxing.get_day_key(now),
                "current_prediction": "炼制",
                "current_prediction_until": now + 3600,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 12,
                "auto_next_time": now - 1,
            }
            state_module.state["tianxing_auto_config"] = {
                "timeline_enabled": True,
                "timeline_dry_run_enabled": False,
                "auto_predict_enabled": True,
                "craft_farm_enabled": True,
                "craft_farm_dry_run_enabled": False,
                "craft_farm_off_window_enabled": False,
                "farm_route": "炼制",
                "farm_window_enabled": True,
                "farm_windows_text": "02:00-03:00",
            }
            state_module.state["tianxing_timeline_state"] = {
                "plan_id": "test-release",
                "phase": "waiting_send",
                "route": "炼制",
                "active_step_index": 0,
                "active_step": {
                    "action": "release_downstream",
                    "arg": "炼制",
                    "route": "炼制",
                    "status": "pending",
                    "release_basis": "prediction",
                    "reason": "测试放行",
                },
                "steps": [{
                    "action": "release_downstream",
                    "arg": "炼制",
                    "route": "炼制",
                    "status": "pending",
                    "release_basis": "prediction",
                    "reason": "测试放行",
                }],
                "released_routes": {},
            }
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command") as send_mock:
                await tianxing.run_tianxing_scheduler(now)
            timeline = tianxing.normalize_tianxing_timeline_state(state_module.state["tianxing_timeline_state"])
            observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])

        send_mock.assert_not_called()
        with state_module.use_identity(self.identity_id):
            self.assertTrue(tianxing.is_tianxing_route_released("炼制", now=now + 1))
        self.assertEqual("downstream_released", timeline["phase"])
        self.assertEqual("craft_farm", observed["auto_last_action"])

    async def test_scheduler_waits_for_pending_auto_panel_without_resend(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": 0,
                "auto_next_time": now - 1,
                "auto_pending_action": "panel",
                "auto_pending_command": ".天机盘",
                "auto_pending_msg_id": 9101,
                "auto_pending_sent_at": now - 10,
                "auto_pending_due_at": now + 80,
            }
            with patch.object(tianxing, "send_game_command") as send_mock:
                await tianxing.run_tianxing_scheduler(now)
            observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])

        send_mock.assert_not_called()
        self.assertEqual("panel", observed["auto_pending_action"])
        self.assertEqual(9101, observed["auto_pending_msg_id"])

    async def test_scheduler_pending_auto_panel_timeout_backs_off_without_resend(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": 0,
                "auto_next_time": now - 1,
                "auto_pending_action": "panel",
                "auto_pending_command": ".天机盘",
                "auto_pending_msg_id": 9101,
                "auto_pending_sent_at": now - 120,
                "auto_pending_due_at": now - 1,
            }
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command") as send_mock:
                await tianxing.run_tianxing_scheduler(now)
            observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])

        send_mock.assert_not_called()
        self.assertEqual("", observed["auto_pending_action"])
        self.assertIn("回复超时", observed["auto_last_error"])
        self.assertGreater(observed["auto_next_time"], now)

    async def test_scheduler_manual_pause_clears_pending_without_sending(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "auto_next_time": now - 1,
                "auto_pending_action": "panel",
                "auto_pending_command": ".天机盘",
                "auto_pending_msg_id": 9101,
                "auto_pending_sent_at": now - 120,
                "auto_pending_due_at": now - 1,
                "automation_paused_until": -1,
                "automation_paused_reason": "手动测试",
            }
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command") as send_mock:
                await tianxing.run_tianxing_scheduler(now)
            observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])

        send_mock.assert_not_called()
        self.assertEqual("", observed["auto_pending_action"])
        self.assertEqual(0, observed["auto_pending_msg_id"])
        self.assertEqual("paused", observed["auto_last_action"])
        self.assertIn("已暂停", observed["auto_last_error"])

    def test_passive_panel_reply_clears_pending_auto_action(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "auto_pending_action": "panel",
                "auto_pending_command": ".天机盘",
                "auto_pending_msg_id": 9101,
                "auto_pending_sent_at": now - 5,
                "auto_pending_due_at": now + 85,
            }
            changed = tianxing.apply_tianxing_passive(
                real_text("tianxing.panel.basic"),
                now=now,
                family="tianxing_panel",
            )
            observed = tianxing.normalize_tianxing_observation(state_module.state["tianxing_observation"])

        self.assertTrue(changed)
        self.assertEqual("", observed["auto_pending_action"])
        self.assertEqual(0, observed["auto_pending_msg_id"])

    async def test_scheduler_observes_when_recent_panel_has_no_star_choices(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "available_stars": [],
            "fixed_star": "",
            "calamity_count": 0,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".观命", send_mock.await_args.args[0])
        self.assertEqual("observe", observed["auto_last_action"])

    async def test_scheduler_clears_calamity_but_never_auto_sets_predicts_or_changes_fate(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "available_stars": ["天府", "太阴"],
            "fixed_star": "",
            "calamity_count": 2,
            "tianji_value": 63,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".消劫", send_mock.await_args.args[0])
        self.assertEqual("clear_calamity", observed["auto_last_action"])
        self.assertEqual(2, observed["calamity_count"])

        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "available_stars": ["天府", "太阴"],
            "fixed_star": "",
            "calamity_count": 0,
            "tianji_value": 63,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".观命", send_mock.await_args.args[0])
        self.assertEqual("observe", observed["auto_last_action"])

    async def test_scheduler_dry_runs_strategic_actions_until_operator_allows_send(self):
        now = 1_780_000_000.0
        observation = {
            "last_observed_at": now - 60,
            "available_stars": ["天府", "太阴"],
            "fixed_star": "太阴",
            "current_prediction": "",
            "current_prediction_until": 0,
            "calamity_count": 0,
            "tianji_value": 63,
            "auto_next_time": now - 1,
        }
        config = {
            "auto_predict_enabled": True,
            "strategy_dry_run_enabled": True,
            "predict_route": "探索",
        }

        send_mock, observed = await self._run_with_observation(observation, now=now, config=config)

        send_mock.assert_not_called()
        self.assertEqual("timeline_required", observed["auto_last_action"])
        self.assertIn("时间线规划器授权", observed["auto_last_error"])

    async def test_scheduler_sends_strategic_action_only_when_dry_run_disabled(self):
        now = 1_780_000_000.0
        observation = {
            "last_observed_at": now - 60,
            "available_stars": ["天府", "太阴"],
            "available_stars_source": "observe",
            "fixed_star": "",
            "current_prediction": "",
            "current_prediction_until": 0,
            "calamity_count": 0,
            "tianji_value": 63,
            "auto_next_time": now - 1,
        }
        config = {
            "auto_set_star_enabled": True,
            "strategy_dry_run_enabled": False,
            "star_priority": ["太阴", "天府"],
        }

        send_mock, observed = await self._run_with_observation(observation, now=now, config=config)

        send_mock.assert_awaited_once()
        self.assertEqual(".定命 太阴", send_mock.await_args.args[0])
        self.assertEqual("set_star", observed["auto_last_action"])
        self.assertEqual(".定命 太阴", observed["auto_last_plan"])

    async def test_scheduler_does_not_direct_send_set_star_when_timeline_enabled(self):
        now = 1_780_000_000.0
        observation = {
            "last_observed_at": now - 60,
            "available_stars": ["天府", "太阴"],
            "available_stars_source": "observe",
            "fixed_star": "",
            "current_prediction": "",
            "current_prediction_until": 0,
            "calamity_count": 0,
            "tianji_value": 63,
            "auto_next_time": now - 1,
        }
        config = {
            "timeline_enabled": True,
            "auto_set_star_enabled": True,
            "strategy_dry_run_enabled": False,
            "star_priority": ["太阴", "天府"],
        }

        send_mock, observed = await self._run_with_observation(observation, now=now, config=config)

        send_mock.assert_not_called()
        self.assertEqual("timeline_required", observed["auto_last_action"])
        self.assertIn("时间线规划器授权", observed["auto_last_error"])

    async def test_scheduler_does_not_run_broad_farm_timeline_without_active_farm(self):
        now = 1_780_000_000.0
        observation = {
            "last_observed_at": now - 60,
            "available_stars": ["天府", "太阴"],
            "available_stars_source": "observe",
            "fixed_star": "",
            "current_prediction": "",
            "current_prediction_until": 0,
            "calamity_count": 0,
            "tianji_value": 3,
            "auto_next_time": now - 1,
        }
        config = {
            "timeline_enabled": True,
            "timeline_dry_run_enabled": False,
            "auto_set_star_enabled": True,
            "strategy_dry_run_enabled": False,
            "star_priority": ["太阴", "天府"],
            "farm_window_enabled": True,
            "farm_window_start": time.strftime("%H:%M", time.localtime(now)),
            "farm_window_duration_min": 60,
        }
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = observation
            state_module.state["tianxing_auto_config"] = config
            with patch.object(tianxing, "save_state"), \
                 patch.object(tianxing, "send_game_command") as send_mock, \
                 patch.object(
                     tianxing,
                     "run_tianxing_timeline_scheduler",
                     new=AsyncMock(return_value={"phase": "sent_waiting_ack", "changed": True, "reason": "时间线步骤已发送。"}),
                 ) as timeline_mock:
                await tianxing.run_tianxing_scheduler(now)
            observed = state_module.state["tianxing_observation"]

        send_mock.assert_not_called()
        timeline_mock.assert_not_awaited()
        self.assertEqual("timeline_required", observed["auto_last_action"])
        self.assertIn("时间线规划器授权", observed["auto_last_error"])

    async def test_scheduler_bypasses_future_auto_time_for_pending_farm_timeline(self):
        now = 1_780_000_000.0
        observation = {
            "last_observed_at": now - 60,
            "available_stars": ["紫微", "贪狼", "天府"],
            "available_stars_source": "panel",
            "fixed_star": "贪狼",
            "current_prediction": "",
            "current_prediction_until": 0,
            "current_change": "",
            "current_change_until": 0,
            "calamity_count": 0,
            "tianji_value": 3,
            "auto_next_time": now + 6 * 3600,
        }
        config = {
            "timeline_enabled": True,
            "timeline_dry_run_enabled": False,
            "auto_predict_enabled": True,
            "strategy_dry_run_enabled": False,
            "craft_farm_enabled": True,
            "craft_farm_dry_run_enabled": False,
            "craft_farm_daily_limit": 42,
            "craft_farm_item": "玄铁剑",
            "farm_route": "炼制",
            "farm_window_enabled": True,
            "farm_window_start": time.strftime("%H:%M", time.localtime(now)),
            "farm_window_duration_min": 60,
        }
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = observation
            state_module.state["tianxing_auto_config"] = config
            with patch.object(tianxing, "save_state"), \
                 patch.object(tianxing, "send_game_command") as send_mock, \
                 patch.object(
                     tianxing,
                     "run_tianxing_timeline_scheduler",
                     new=AsyncMock(return_value={"phase": "sent_waiting_ack", "changed": True, "reason": "时间线步骤已发送。"}),
                 ) as timeline_mock:
                await tianxing.run_tianxing_scheduler(now)
            observed = state_module.state["tianxing_observation"]

        send_mock.assert_not_called()
        self.assertGreaterEqual(timeline_mock.await_count, 1)
        self.assertEqual("craft_farm", observed["auto_last_action"])
        self.assertEqual("timeline_required", observed["auto_last_plan"])

    async def test_scheduler_bypasses_future_auto_time_for_confirmed_farm_timeline(self):
        now = 1_780_000_000.0
        observation = {
            "last_observed_at": now - 60,
            "available_stars": ["太阴", "贪狼", "天府"],
            "available_stars_source": "panel",
            "fixed_star": "太阴",
            "current_prediction": "炼制",
            "current_prediction_until": now + 3600,
            "current_change": "",
            "current_change_until": 0,
            "calamity_count": 0,
            "tianji_value": 3,
            "auto_next_time": now + 6 * 3600,
        }
        config = {
            "timeline_enabled": True,
            "timeline_dry_run_enabled": False,
            "auto_predict_enabled": True,
            "strategy_dry_run_enabled": False,
            "craft_farm_enabled": True,
            "craft_farm_dry_run_enabled": False,
            "craft_farm_daily_limit": 42,
            "craft_farm_item": "玄铁剑",
            "farm_route": "炼制",
            "farm_window_enabled": True,
            "farm_window_start": time.strftime("%H:%M", time.localtime(now)),
            "farm_window_duration_min": 60,
        }
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = observation
            state_module.state["tianxing_auto_config"] = config
            state_module.state["tianxing_timeline_state"] = {
                "phase": "state_confirmed",
                "route": "炼制",
                "active_step_index": 0,
                "active_step": {"action": "predict", "arg": "炼制", "route": "炼制", "status": "confirmed"},
                "steps": [{"action": "predict", "arg": "炼制", "route": "炼制", "status": "confirmed"}],
            }
            with patch.object(tianxing, "save_state"), \
                 patch.object(tianxing, "send_game_command") as send_mock, \
                 patch.object(
                     tianxing,
                     "run_tianxing_timeline_scheduler",
                     new=AsyncMock(return_value={"phase": "waiting_send", "changed": True, "reason": "时间线推进到下一步。"}),
                 ) as timeline_mock:
                await tianxing.run_tianxing_scheduler(now)
            observed = state_module.state["tianxing_observation"]

        send_mock.assert_not_called()
        self.assertGreaterEqual(timeline_mock.await_count, 1)
        self.assertEqual("craft_farm", observed["auto_last_action"])
        self.assertEqual("timeline_required", observed["auto_last_plan"])
        self.assertEqual(now + tianxing.TIANXING_CRAFT_FARM_RETRY_SEC, observed["auto_next_time"])

    async def test_scheduler_uses_timeline_ack_due_for_sent_farm_step(self):
        now = 1_780_000_000.0
        ack_due_at = now + 90
        observation = {
            "last_observed_at": now - 60,
            "available_stars": ["太阴", "贪狼", "天府"],
            "available_stars_source": "panel",
            "fixed_star": "太阴",
            "current_prediction": "",
            "current_prediction_until": 0,
            "current_change": "",
            "current_change_until": 0,
            "calamity_count": 0,
            "tianji_value": 3,
            "auto_next_time": now + 6 * 3600,
        }
        config = {
            "timeline_enabled": True,
            "timeline_dry_run_enabled": False,
            "auto_predict_enabled": True,
            "strategy_dry_run_enabled": False,
            "craft_farm_enabled": True,
            "craft_farm_dry_run_enabled": False,
            "craft_farm_daily_limit": 42,
            "craft_farm_item": "玄铁剑",
            "farm_route": "炼制",
            "farm_window_enabled": True,
            "farm_window_start": time.strftime("%H:%M", time.localtime(now)),
            "farm_window_duration_min": 60,
        }

        async def fake_timeline_scheduler(*_args, **_kwargs):
            state_module.state["tianxing_timeline_state"] = {
                "phase": "sent_waiting_ack",
                "route": "炼制",
                "active_step_index": 0,
                "active_step": {
                    "action": "predict",
                    "arg": "炼制",
                    "route": "炼制",
                    "status": "sent_waiting_ack",
                    "ack_due_at": ack_due_at,
                },
                "steps": [],
            }
            return {"phase": "sent_waiting_ack", "changed": True, "reason": "等待天星前置命令真实回复。"}

        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = observation
            state_module.state["tianxing_auto_config"] = config
            with patch.object(tianxing, "save_state"), \
                 patch.object(tianxing, "send_game_command") as send_mock, \
                 patch.object(
                     tianxing,
                     "run_tianxing_timeline_scheduler",
                     new=AsyncMock(side_effect=fake_timeline_scheduler),
                 ) as timeline_mock:
                await tianxing.run_tianxing_scheduler(now)
            observed = state_module.state["tianxing_observation"]

        send_mock.assert_not_called()
        timeline_mock.assert_awaited_once()
        self.assertEqual("craft_farm", observed["auto_last_action"])
        self.assertEqual("timeline_required", observed["auto_last_plan"])
        self.assertEqual(ack_due_at, observed["auto_next_time"])

    async def test_scheduler_bypasses_future_auto_time_for_blocked_replan(self):
        now = 1_780_000_000.0
        observation = {
            "last_observed_at": now - 60,
            "available_stars": ["太阴", "贪狼", "天府"],
            "available_stars_source": "panel",
            "fixed_star": "太阴",
            "current_prediction": "",
            "current_prediction_until": 0,
            "current_change": "",
            "current_change_until": 0,
            "calamity_count": 0,
            "tianji_value": 8,
            "auto_next_time": now + 6 * 3600,
        }
        config = {
            "timeline_enabled": True,
            "timeline_dry_run_enabled": False,
            "auto_predict_enabled": True,
            "strategy_dry_run_enabled": False,
            "craft_farm_enabled": True,
            "craft_farm_dry_run_enabled": False,
            "craft_farm_daily_limit": 42,
            "craft_farm_item": "玄铁剑",
            "farm_route": "炼制",
            "farm_window_enabled": True,
            "farm_window_start": time.strftime("%H:%M", time.localtime(now)),
            "farm_window_duration_min": 60,
        }
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = observation
            state_module.state["tianxing_auto_config"] = config
            state_module.state["tianxing_timeline_state"] = {
                "phase": "blocked_replan",
                "route": "炼制",
                "active_step_index": -1,
                "active_step": {},
                "blocked_until": now - 1,
                "last_error": "校准已完成，需重算时间线。",
            }
            with patch.object(tianxing, "save_state"), \
                 patch.object(tianxing, "send_game_command") as send_mock, \
                 patch.object(
                     tianxing,
                     "run_tianxing_timeline_scheduler",
                     new=AsyncMock(return_value={"phase": "waiting_send", "changed": True, "reason": "时间线已重算。"}),
                 ) as timeline_mock:
                await tianxing.run_tianxing_scheduler(now)
            observed = state_module.state["tianxing_observation"]

        send_mock.assert_not_called()
        timeline_mock.assert_awaited_once()
        self.assertEqual("craft_farm", observed["auto_last_action"])
        self.assertEqual("timeline_required", observed["auto_last_plan"])
        self.assertEqual(now + tianxing.TIANXING_CRAFT_FARM_RETRY_SEC, observed["auto_next_time"])

    async def test_scheduler_respects_future_auto_time(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": [],
                "available_stars_day": tianxing.get_day_key(now),
                "fixed_star": "",
                "fixed_star_day": "",
                "auto_next_time": now + 300,
            }
            with patch.object(tianxing, "send_game_command") as send_mock:
                await tianxing.run_tianxing_scheduler(now)

            send_mock.assert_not_called()

    async def test_scheduler_blocks_dirty_time_fields_without_clearing_or_saving(self):
        now = 1_780_000_000.0
        dirty_cases = (
            ("auto_next_time", "nan"),
            ("last_observed_at", "观测时间异常"),
            ("current_prediction_until", "inf"),
            ("current_change_until", "-inf"),
        )
        for field_name, dirty_value in dirty_cases:
            with self.subTest(field_name=field_name):
                observation = {
                    "last_observed_at": now - 60,
                    "available_stars": [],
                    "fixed_star": "",
                    "calamity_count": 0,
                    "auto_next_time": now - 1,
                }
                observation[field_name] = dirty_value
                with state_module.use_identity(self.identity_id):
                    state_module.state["tianxing_enabled"] = True
                    state_module.state["tianxing_observation"] = observation
                    with (
                        patch.object(tianxing, "save_state") as save_mock,
                        patch.object(tianxing, "send_game_command") as send_mock,
                    ):
                        await tianxing.run_tianxing_scheduler(now)

                    send_mock.assert_not_called()
                    save_mock.assert_not_called()
                    self.assertEqual(dirty_value, state_module.state["tianxing_observation"][field_name])

    async def test_deep_retreat_gate_does_not_create_close_change_fate_by_default(self):
        now = local_ts(1, 55, year=2026, month=6, day=30)
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_auto_config"] = {
                "timeline_enabled": True,
                "timeline_dry_run_enabled": False,
                "auto_change_fate_enabled": True,
                "deep_retreat_consume_enabled": False,
                "route_prepare_lead_sec": 300,
            }
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": ["紫微", "贪狼", "天府"],
                "available_stars_day": tianxing.get_day_key(now),
                "fixed_star": "",
                "fixed_star_day": "",
                "current_prediction": "",
                "current_prediction_until": 0,
                "current_change": "",
                "current_change_until": 0,
                "tianji_value": 36,
            }
            state_module.state["tianxing_timeline_state"] = {}
            state_module.state["deep_retreat_phase"] = "running"
            state_module.state["next_deep_retreat_time"] = now + 60
            with (
                patch.object(deep_retreat, "save_state") as save_mock,
                patch.object(
                    deep_retreat,
                    "run_tianxing_timeline_scheduler",
                    new=AsyncMock(return_value={"phase": "sent_waiting_ack", "changed": True}),
                ) as timeline_mock,
            ):
                allowed = await deep_retreat._run_deep_retreat_tianxing_gate(now)
            next_time = state_module.state["next_deep_retreat_time"]

        self.assertTrue(allowed)
        self.assertEqual(now + 60, next_time)
        timeline_mock.assert_not_awaited()
        save_mock.assert_not_called()


class TianxingPassiveInboxTests(unittest.TestCase):
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

    def _prepare_identity(self, send_as_id=2001, username="PeggyArmstrong_a776"):
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(
            send_as_id,
            username=username,
            label=username,
            sect_name="天星宗",
        )
        with state_module.use_identity(send_as_id):
            state_module.state["tianxing_enabled"] = True
        return send_as_id

    def test_passive_inbox_updates_tianxing_from_reply_context(self):
        send_as_id = self._prepare_identity()
        event = SimpleNamespace(chat_id=-1001680975844, id=9706484)
        with state_module.use_identity(send_as_id):
            state_module.state["tianxing_observation"] = {
                "current_prediction": "炼制",
                "current_prediction_until": 1_780_100_000.0,
                "current_change": "探索",
                "current_change_until": 1_780_200_000.0,
            }
        action_guard.note_sent(".天机盘", send_as_id, 9706481, sent_at=1_779_999_990.0)
        self.assertIn("tianxing_panel", action_guard.get_action_guard_sessions(send_as_id))

        with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
            handled = asyncio.run(passive_inbox.handle_passive_module_card(
                real_text("tianxing.panel.basic"),
                now=1_780_000_000.0,
                reply_context={
                    "send_as_id": send_as_id,
                    "family": "tianxing_panel",
                    "reply_to_msg_id": 9706481,
                    "root_msg_id": 9706481,
                },
                event=event,
                event_type="message",
            ))

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            observed = state_module.state["tianxing_observation"]
            self.assertEqual("天机盘", observed["last_action"])
            self.assertEqual(63, observed["tianji_value"])
            self.assertEqual("", observed["current_prediction"])
            self.assertEqual(0, observed["current_prediction_until"])
            self.assertEqual("", observed["current_change"])
            self.assertEqual(0, observed["current_change_until"])
            self.assertNotIn("tianxing_panel", state_module.state["action_guard_sessions"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["changed"])
        self.assertEqual(1, snapshot["modules"]["tianxing"])

    def test_passive_inbox_can_route_at_tagged_modifier_without_reply_context(self):
        send_as_id = self._prepare_identity(username="PeggyArmstrong_a776")
        event = SimpleNamespace(chat_id=-1001680975844, id=9707995)

        with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
            handled = asyncio.run(passive_inbox.handle_passive_module_card(
                real_text("tianxing.modifier.wild"),
                now=1_780_000_000.0,
                reply_context=None,
                event=event,
                event_type="message",
            ))

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            observed = state_module.state["tianxing_observation"]
            self.assertEqual("prediction_hit", observed["last_result"])
            self.assertEqual(30, observed["last_contrib_gain"])

    def test_apply_modifier_consumes_prediction_miss_and_keeps_waiting_change_state(self):
        send_as_id = self._prepare_identity(username="PeggyArmstrong_a776")
        now = 1_780_000_000.0

        with state_module.use_identity(send_as_id):
            state_module.state["tianxing_observation"] = {
                "current_prediction": "炼制",
                "current_prediction_until": now + 3600,
                "current_change": "探索",
                "current_change_until": now + 7200,
                "calamity_count": 2,
            }
            changed = tianxing.apply_tianxing_passive(real_text("tianxing.modifier.prediction_miss"), now=now)
            observed = state_module.state["tianxing_observation"]

        self.assertTrue(changed)
        self.assertEqual("prediction_miss", observed["last_result"])
        self.assertEqual("", observed["current_prediction"])
        self.assertEqual(0, observed["current_prediction_until"])
        self.assertEqual("炼制", observed["prediction_consumed_route"])
        self.assertEqual(now, observed["prediction_consumed_at"])
        self.assertEqual("探索", observed["current_change"])
        self.assertGreater(observed["current_change_until"], now)
        self.assertEqual(3, observed["calamity_count"])

        with state_module.use_identity(send_as_id):
            state_module.state["tianxing_observation"].update({
                "current_prediction": "探索",
                "current_prediction_until": now + 3600,
                "current_change": "探索",
                "current_change_until": now + 7200,
            })
            changed = tianxing.apply_tianxing_passive(real_text("tianxing.modifier.change_triggered"), now=now)
            observed = state_module.state["tianxing_observation"]

        self.assertTrue(changed)
        self.assertEqual("change_triggered", observed["last_result"])
        self.assertEqual("", observed["current_prediction"])
        self.assertEqual(0, observed["current_prediction_until"])
        self.assertEqual("探索", observed["prediction_consumed_route"])
        self.assertEqual(now, observed["prediction_consumed_at"])
        self.assertEqual("", observed["current_change"])
        self.assertEqual(0, observed["current_change_until"])

    def test_real_message_fixture_includes_tianxing_samples(self):
        samples = list(iter_real_message_samples(FIXTURE_PATH, module="tianxing"))

        self.assertGreaterEqual(len(samples), 9)
        self.assertTrue(all(sample.family.startswith("tianxing_") for sample in samples))


if __name__ == "__main__":
    unittest.main()
