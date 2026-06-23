import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model.features import fishing


FISHING_START_TEXT = """【灵溪垂钓】
钓者：@WalterWA2000
鱼塘：青溪浅滩
天象：小雨
鱼讯：静候鱼讯
进度：□□□□□□□□□□ 0%

你挂上 【灵米饵】，抛竿入水，敛息坐定。
预计 47秒 内会有鱼讯。
可用：.钓鱼状态 / .收竿"""

FISHING_WAIT_TEXT = """【灵溪垂钓】
钓者：@WalterWA2000
鱼塘：青溪浅滩
天象：小雨
鱼讯：静候鱼讯
进度：■■■■□□□□□□ 36%

鱼讯未至，还需 9秒。

可用：.钓鱼状态 / .收竿
鱼讯倒计时：9秒"""

FISHING_BITE_TEXT = """【灵溪垂钓】
钓者：@WalterWA2000
鱼塘：青溪浅滩
天象：小雨
鱼讯：鱼在试口
进度：■■■■■■■□□□ 67%

鱼讯已至，请在 33秒 内 .提竿。

可用：.试探咬饵 / .提竿 / .收竿
提竿剩余：33秒"""

FISHING_BLACK_FLOAT_TEXT = """【灵溪垂钓】
钓者：@WalterWA2000
鱼塘：青溪浅滩
天象：小雨
鱼讯：正口黑漂
进度：■■■■■■■■□□ 76%

你手腕微沉，只放半寸灵线。浮漂先停一息，随后猛地一黑。

可用：.试探咬饵 / .提竿 / .收竿
提竿剩余：23秒"""

FISHING_CATCH_TEXT = """【提竿成功】
@WalterWA2000 在 青溪浅滩 猛然提竿，灵线绷成一道银弧。
水下灵光一翻，竟是一尾 【银须灵鲢】！

品阶：灵鱼
重量：1.54斤
钓术：Lv.0 凡竿 (+4)


鱼获已入鱼篓，可用 .开鱼 银须灵鲢 查看鱼腹机缘。"""

OPEN_FISH_TEXT = """【剖鱼取机缘】
你剖开 【银须灵鲢】x1，鱼腹中灵光微闪。

获得：灵石x28、灵鱼肉x1、灵鱼鳞x1、清灵草x1、修为+39"""

OPEN_FISH_WITH_LOG_PREFIX_TEXT = """[2026/6/23 01:37] 韩天尊: 【剖鱼取机缘】
你剖开 【银须灵鲢】x1，鱼腹中灵光微闪。

获得：灵石x28、灵鱼肉x1、灵鱼鳞x1、清灵草x1、修为+39"""


class FishingLabTests(unittest.TestCase):
    def test_parse_fishing_start_status(self):
        status = fishing.parse_fishing_status(FISHING_START_TEXT)

        self.assertEqual("@WalterWA2000", status.angler)
        self.assertEqual("青溪浅滩", status.pond)
        self.assertEqual("小雨", status.weather)
        self.assertEqual("静候鱼讯", status.signal)
        self.assertEqual(0, status.progress_percent)
        self.assertEqual("灵米饵", status.bait)
        self.assertEqual("", status.suggested_command)
        self.assertIn(".钓鱼状态", status.available_commands)

    def test_parse_fishing_wait_status(self):
        status = fishing.parse_fishing_status(FISHING_WAIT_TEXT)

        self.assertEqual("静候鱼讯", status.signal)
        self.assertEqual(36, status.progress_percent)
        self.assertEqual(9, status.wait_seconds)
        self.assertEqual("", status.suggested_command)

    def test_parse_bite_status_suggests_probe_only(self):
        status = fishing.parse_fishing_status(FISHING_BITE_TEXT)

        self.assertEqual("鱼在试口", status.signal)
        self.assertEqual(33, status.lift_seconds)
        self.assertEqual(".提竿", status.suggested_command)

    def test_parse_bite_status_suggests_probe_when_enabled(self):
        status = fishing.parse_fishing_status(FISHING_BITE_TEXT, auto_probe_enabled=True)

        self.assertEqual("鱼在试口", status.signal)
        self.assertEqual(".试探咬饵", status.suggested_command)

    def test_parse_black_float_status_suggests_raise(self):
        status = fishing.parse_fishing_status(FISHING_BLACK_FLOAT_TEXT)

        self.assertEqual("正口黑漂", status.signal)
        self.assertEqual(76, status.progress_percent)
        self.assertEqual(23, status.lift_seconds)
        self.assertEqual(".提竿", status.suggested_command)

    def test_parse_fishing_catch(self):
        catch = fishing.parse_fishing_catch(FISHING_CATCH_TEXT)

        self.assertEqual("@WalterWA2000", catch.angler)
        self.assertEqual("银须灵鲢", catch.fish)
        self.assertEqual("灵鱼", catch.grade)
        self.assertEqual(1.54, catch.weight_jin)
        self.assertEqual(4, catch.skill_gain)
        self.assertEqual(".开鱼 银须灵鲢", catch.open_command)

    def test_parse_daily_limit_text(self):
        result = fishing.parse_fishing_daily_limit_reached("你今日已垂钓 20/20 竿，神识已乏，明日再来。")

        self.assertEqual(20, result.used)
        self.assertEqual(20, result.limit)

    def test_parse_in_progress_text(self):
        self.assertTrue(fishing.parse_fishing_in_progress_reply("你已有一竿尚未收起。可用 .钓鱼状态 查看，或 .收竿 放弃。"))

    def test_parse_no_active_fishing_text(self):
        self.assertTrue(fishing.parse_no_active_fishing_reply("你当前没有正在进行的垂钓。"))

    def test_parse_open_fish_rewards(self):
        result = fishing.parse_open_fish_result(OPEN_FISH_TEXT)

        self.assertEqual("银须灵鲢", result.fish)
        self.assertEqual(1, result.count)
        self.assertEqual(
            {"灵石": 28, "灵鱼肉": 1, "灵鱼鳞": 1, "清灵草": 1},
            result.items,
        )
        self.assertEqual(39, result.xiuwei_gain)

    def test_parse_open_fish_rewards_with_log_prefix(self):
        result = fishing.parse_open_fish_result(OPEN_FISH_WITH_LOG_PREFIX_TEXT)

        self.assertEqual("银须灵鲢", result.fish)
        self.assertEqual(1, result.count)
        self.assertEqual(28, result.items["灵石"])
        self.assertEqual(1, result.items["清灵草"])
        self.assertEqual(39, result.xiuwei_gain)

    def test_parse_chum_shortage_exposes_internal_cost_key(self):
        cost = fishing.parse_chum_shortage("打窝失败，资源不足：item_fishing_bait_plainx2。")

        self.assertIsNotNone(cost)
        self.assertEqual("item_fishing_bait_plain", cost.item_key)
        self.assertEqual(2, cost.count)

    def test_chum_shortage_keys_translate_to_bait_names(self):
        spirit_rice = fishing.parse_chum_shortage("打窝失败，资源不足：item_fishing_bait_spirit_ricex3。")
        demon_blood = fishing.parse_chum_shortage("打窝失败，资源不足：item_fishing_bait_demon_bloodx2。")

        self.assertEqual("灵米饵", fishing.fishing_bait_name_for_item_key(spirit_rice.item_key))
        self.assertEqual("妖血饵", fishing.fishing_bait_name_for_item_key(demon_blood.item_key))

    def test_parse_generic_resource_shortage(self):
        shortage = fishing.parse_generic_resource_shortage("购买失败，当前灵石不足。")

        self.assertIsNotNone(shortage)
        self.assertEqual("灵石不足", shortage.label)
        self.assertEqual("妖丹不足", fishing.parse_generic_resource_shortage("购买失败：妖丹不足。").label)

    def test_parse_buy_bait_success_and_missing_bait_reply(self):
        result = fishing.parse_buy_bait_result("【渔具铺】\n你购得 【灵虫饵】x1。\n老渔修低声道：水静时别急，黑漂时别怂。")

        self.assertEqual("灵虫饵", result.bait)
        self.assertEqual(1, result.count)
        self.assertEqual("灵虫饵", fishing.parse_missing_bait_reply("你的鱼篓中没有【灵虫饵】。可用 .买鱼饵 灵虫饵 购买。"))

    def test_parse_fishing_basket_baits_and_fish(self):
        basket = fishing.parse_fishing_basket(
            "【鱼篓】\n"
            "青竹钓竿：已持有\n"
            "钓术：Lv.1 垂纶（111熟练度）\n"
            "今日竿数：20/20\n"
            "当前窝料：无\n\n"
            "鱼饵\n"
            "- 灵米饵 x1\n\n"
            "鱼获\n"
            "- 银须灵鲢 x1\n\n"
            "可用 .开鱼 <鱼名> [数量] 查看鱼腹机缘。"
        )

        self.assertTrue(basket.rod_owned)
        self.assertEqual("Lv.1 垂纶（111熟练度）", basket.skill)
        self.assertEqual(20, basket.daily_rods_used)
        self.assertEqual(20, basket.daily_rods_limit)
        self.assertEqual({"灵米饵": 1}, basket.baits)
        self.assertEqual({"银须灵鲢": 1}, basket.fish)

    def test_lingcao_chum_cost_is_known_from_shortage_text_not_success_text(self):
        success_text = "【打窝已成】\n你在水脉交汇处撒下 【灵草窝】，接下来 5 竿会受其牵引。"

        self.assertIsNone(fishing.parse_chum_shortage(success_text))
        self.assertEqual("item_fishing_bait_spirit_rice", fishing.get_known_chum_cost("灵草窝").item_key)

    def test_auto_chum_fails_closed_when_cost_is_unknown(self):
        decision = fishing.decide_chum_send("未知窝", auto_chum_enabled=True)

        self.assertFalse(decision.allow_send)
        self.assertEqual("unknown_chum_cost", decision.reason)
        self.assertIsNone(decision.cost)

    def test_known_mikang_chum_can_be_considered_only_when_enabled(self):
        disabled = fishing.decide_chum_send("米糠小窝", auto_chum_enabled=False)
        enabled = fishing.decide_chum_send("米糠小窝", auto_chum_enabled=True)

        self.assertFalse(disabled.allow_send)
        self.assertEqual("auto_chum_disabled", disabled.reason)
        self.assertTrue(enabled.allow_send)
        self.assertEqual("item_fishing_bait_plain", enabled.cost.item_key)
        self.assertEqual(2, enabled.cost.count)

    def test_fishing_config_keeps_pond_bait_and_chum_operator_selectable(self):
        config = fishing.normalize_fishing_config(
            "灵眼寒潭",
            "灵虫饵",
            auto_chum_enabled=True,
            chum_name="米糠小窝",
        )
        plan = fishing.plan_fishing_commands(config, bait_inventory={"灵虫饵": 1, "凡饵": 2, "灵石": 30})

        self.assertEqual("灵眼寒潭", config.pond)
        self.assertEqual("灵虫饵", config.bait)
        self.assertEqual("米糠小窝", config.chum_name)
        self.assertTrue(plan.allow_start)
        self.assertEqual((".打窝 米糠小窝", ".钓鱼 灵眼寒潭 灵虫饵"), plan.commands)

    def test_fishing_plan_can_buy_missing_chum_and_fishing_bait_first(self):
        config = fishing.normalize_fishing_config(
            "青溪浅滩",
            "灵米饵",
            auto_chum_enabled=True,
            chum_name="灵草窝",
            auto_buy_bait_enabled=True,
        )
        plan = fishing.plan_fishing_commands(config, bait_inventory={"灵米饵": 1, "灵石": 1000, "凝血草": 5})

        self.assertTrue(plan.allow_start)
        self.assertEqual((".买鱼饵 灵米饵 20",), plan.purchase_commands)
        self.assertEqual((".买鱼饵 灵米饵 20", ".打窝 灵草窝", ".钓鱼 青溪浅滩 灵米饵"), plan.commands)

    def test_fishing_plan_can_buy_different_fishing_and_chum_baits(self):
        config = fishing.normalize_fishing_config(
            "灵眼寒潭",
            "灵虫饵",
            auto_chum_enabled=True,
            chum_name="灵草窝",
            auto_buy_bait_enabled=True,
        )
        plan = fishing.plan_fishing_commands(
            config,
            bait_inventory={"灵虫饵": 0, "灵米饵": 0, "灵石": 3000, "凝血草": 50},
        )

        self.assertTrue(plan.allow_start)
        self.assertEqual((".买鱼饵 灵虫饵 20", ".买鱼饵 灵米饵 20"), plan.purchase_commands)
        self.assertEqual(
            (".买鱼饵 灵虫饵 20", ".买鱼饵 灵米饵 20", ".打窝 灵草窝", ".钓鱼 灵眼寒潭 灵虫饵"),
            plan.commands,
        )

    def test_fishing_plan_skips_chum_after_daily_chum_limit(self):
        config = fishing.normalize_fishing_config(
            "青溪浅滩",
            "凡饵",
            auto_chum_enabled=True,
            chum_name="米糠小窝",
            auto_buy_bait_enabled=True,
        )
        plan = fishing.plan_fishing_commands(
            config,
            bait_inventory={"凡饵": 1, "灵石": 1000},
            chum_usage_counts={"米糠小窝": 2},
        )

        self.assertTrue(plan.allow_start)
        self.assertEqual((), plan.purchase_commands)
        self.assertEqual((".钓鱼 青溪浅滩 凡饵",), plan.commands)
        self.assertEqual(1, plan.bait_requirements[0].required_count)

    def test_fishing_plan_uses_selected_chums_in_daily_sequence_one_at_a_time(self):
        config = fishing.normalize_fishing_config(
            "青溪浅滩",
            "凡饵",
            auto_chum_enabled=True,
            chum_names=["米糠小窝", "灵草窝", "妖腥窝"],
            auto_buy_bait_enabled=True,
        )
        inventory = {"凡饵": 99, "灵米饵": 99, "妖血饵": 99, "灵石": 10000, "凝血草": 99, "一阶妖丹": 99}

        first = fishing.plan_fishing_commands(config, bait_inventory=inventory, chum_usage_counts={})
        second_mikang = fishing.plan_fishing_commands(config, bait_inventory=inventory, chum_usage_counts={"米糠小窝": 1})
        first_lingcao = fishing.plan_fishing_commands(config, bait_inventory=inventory, chum_usage_counts={"米糠小窝": 2})
        first_yaoxing = fishing.plan_fishing_commands(config, bait_inventory=inventory, chum_usage_counts={"米糠小窝": 2, "灵草窝": 2})
        exhausted = fishing.plan_fishing_commands(config, bait_inventory=inventory, chum_usage_counts={"米糠小窝": 2, "灵草窝": 2, "妖腥窝": 1})

        self.assertEqual(".打窝 米糠小窝", first.commands[0])
        self.assertEqual(".打窝 米糠小窝", second_mikang.commands[0])
        self.assertEqual(".打窝 灵草窝", first_lingcao.commands[0])
        self.assertEqual(".打窝 妖腥窝", first_yaoxing.commands[0])
        self.assertEqual((".钓鱼 青溪浅滩 凡饵",), exhausted.commands)

    def test_fishing_plan_uses_custom_buy_count_and_never_underbuys_missing_amount(self):
        config = fishing.normalize_fishing_config(
            "青溪浅滩",
            "灵米饵",
            auto_chum_enabled=False,
            auto_buy_bait_enabled=True,
            auto_buy_bait_count=12,
        )
        custom_plan = fishing.plan_fishing_commands(config, bait_inventory={"灵米饵": 0})
        self.assertEqual((".买鱼饵 灵米饵 12",), custom_plan.purchase_commands)

        small_batch_config = fishing.normalize_fishing_config(
            "青溪浅滩",
            "灵米饵",
            auto_chum_enabled=True,
            chum_name="灵草窝",
            auto_buy_bait_enabled=True,
            auto_buy_bait_count=2,
        )
        missing_plan = fishing.plan_fishing_commands(small_batch_config, bait_inventory={"灵米饵": 0})
        self.assertEqual((".买鱼饵 灵米饵 4",), missing_plan.purchase_commands)

    def test_fishing_plan_blocks_missing_bait_when_auto_buy_is_disabled(self):
        config = fishing.normalize_fishing_config(
            "青溪浅滩",
            "灵米饵",
            auto_chum_enabled=True,
            chum_name="灵草窝",
            auto_buy_bait_enabled=False,
        )
        plan = fishing.plan_fishing_commands(config, bait_inventory={"灵米饵": 1})

        self.assertFalse(plan.allow_start)
        self.assertEqual((), plan.commands)
        self.assertEqual((".买鱼饵 灵米饵 20",), plan.purchase_commands)
        self.assertEqual("insufficient_bait", plan.blocked_reason)

    def test_fishing_plan_accepts_internal_api_item_keys_as_inventory(self):
        config = fishing.normalize_fishing_config(
            "青溪浅滩",
            "凡饵",
            auto_chum_enabled=True,
            chum_name="米糠小窝",
        )
        plan = fishing.plan_fishing_commands(config, bait_inventory={"item_fishing_bait_plain": 3, "灵石": 30})

        self.assertTrue(plan.allow_start)
        self.assertEqual((), plan.purchase_commands)
        self.assertEqual((".打窝 米糠小窝", ".钓鱼 青溪浅滩 凡饵"), plan.commands)

    def test_fishing_plan_blocks_known_resource_shortage_before_sending(self):
        config = fishing.normalize_fishing_config(
            "青溪浅滩",
            "凡饵",
            auto_chum_enabled=True,
            chum_name="米糠小窝",
        )
        plan = fishing.plan_fishing_commands(config, bait_inventory={"凡饵": 3, "灵石": 12})

        self.assertFalse(plan.allow_start)
        self.assertEqual((), plan.commands)
        self.assertEqual("灵石", plan.resource_requirements[0].item_name)
        self.assertEqual(18, plan.resource_requirements[0].missing_count)
        self.assertIn("insufficient_resources", plan.blocked_reason)

    def test_fishing_plan_blocks_bait_purchase_when_resource_shortage_is_known(self):
        config = fishing.normalize_fishing_config(
            "青溪浅滩",
            "妖血饵",
            auto_chum_enabled=False,
            auto_buy_bait_enabled=True,
            auto_buy_bait_count=8,
        )
        plan = fishing.plan_fishing_commands(config, bait_inventory={"妖血饵": 0, "灵石": 1000, "一阶妖丹": 0})

        self.assertFalse(plan.allow_start)
        self.assertEqual((".买鱼饵 妖血饵 8",), plan.purchase_commands)
        self.assertIn("一阶妖丹x8", plan.blocked_reason)

    def test_fishing_plan_allows_no_chum_choice(self):
        config = fishing.normalize_fishing_config(
            "青溪浅滩",
            "灵米饵",
            auto_chum_enabled=True,
            chum_name="无",
        )
        plan = fishing.plan_fishing_commands(config, bait_inventory={"灵米饵": 1})

        self.assertFalse(config.auto_chum_enabled)
        self.assertEqual("", config.chum_name)
        self.assertTrue(plan.allow_start)
        self.assertEqual((".钓鱼 青溪浅滩 灵米饵",), plan.commands)

    def test_fishing_config_rejects_unknown_ui_choices(self):
        with self.assertRaises(ValueError):
            fishing.normalize_fishing_config("未知鱼塘", "凡饵")
        with self.assertRaises(ValueError):
            fishing.normalize_fishing_config("青溪浅滩", "未知饵")
        with self.assertRaises(ValueError):
            fishing.normalize_fishing_config("青溪浅滩", "凡饵", auto_chum_enabled=True, chum_name="未知窝")

    def test_fishing_behavior_core_has_no_runtime_side_effect_imports(self):
        from model.features import fishing_behavior

        imported_names = set(fishing_behavior.__dict__)
        self.assertNotIn("send_game_command", imported_names)
        self.assertNotIn("save_state", imported_names)
        self.assertNotIn("state", imported_names)

    def test_fishing_behavior_scheduler_emits_command_not_send(self):
        from model.features import fishing_behavior

        snapshot = {
            "fishing_enabled": True,
            "next_fishing_time": 0,
            "fishing_pond": "青溪浅滩",
            "fishing_bait": "凡饵",
            "fishing_daily_limit": 20,
            "fishing_daily_day": "",
            "fishing_daily_count": 0,
        }
        effect = fishing_behavior.decide_scheduler(snapshot, 1_700_000_000.0)

        self.assertTrue(effect.handled)
        self.assertEqual(".钓鱼 青溪浅滩 凡饵", effect.command)
        self.assertIn("fishing_daily_day", effect.updates)
        self.assertFalse(effect.storage_deltas)

    def test_fishing_behavior_scheduler_does_not_auto_open_old_fish(self):
        from model.features import fishing_behavior

        snapshot = {
            "fishing_enabled": True,
            "next_fishing_time": 0,
            "fishing_pond": "青溪浅滩",
            "fishing_bait": "凡饵",
            "fishing_daily_limit": 20,
            "fishing_daily_day": "",
            "fishing_daily_count": 0,
            "fishing_pending_open_fish": "青鳞小鲫",
        }
        effect = fishing_behavior.decide_scheduler(snapshot, 1_700_000_000.0)

        self.assertTrue(effect.handled)
        self.assertNotEqual(".开鱼 青鳞小鲫", effect.command)
        self.assertEqual(".钓鱼 青溪浅滩 凡饵", effect.command)

    def test_fishing_behavior_scheduler_opens_pending_fish_after_daily_limit(self):
        from model.features import fishing_behavior

        snapshot = {
            "fishing_enabled": True,
            "next_fishing_time": 0,
            "fishing_pond": "青溪浅滩",
            "fishing_bait": "凡饵",
            "fishing_daily_limit": 20,
            "fishing_daily_day": fishing_behavior.get_day_key(1_700_000_000.0),
            "fishing_daily_count": 20,
            "fishing_pending_open_fish": '{"银须灵鲢": 2}',
        }
        effect = fishing_behavior.decide_scheduler(snapshot, 1_700_000_000.0)

        self.assertTrue(effect.handled)
        self.assertEqual(".开鱼 银须灵鲢 2", effect.command)

    def test_fishing_behavior_scheduler_recovers_in_progress_rod_with_status(self):
        from model.features import fishing_behavior

        snapshot = {
            "fishing_enabled": True,
            "next_fishing_time": 0,
            "fishing_pond": "青溪浅滩",
            "fishing_bait": "凡饵",
            "fishing_daily_limit": 20,
            "fishing_daily_day": "",
            "fishing_daily_count": 1,
            "fishing_phase": "waiting",
        }
        effect = fishing_behavior.decide_scheduler(snapshot, 1_700_000_000.0)

        self.assertTrue(effect.handled)
        self.assertEqual(".钓鱼状态", effect.command)

    def test_fishing_behavior_scheduler_does_not_treat_opening_as_active_rod(self):
        from model.features import fishing_behavior

        snapshot = {
            "fishing_enabled": True,
            "next_fishing_time": 0,
            "fishing_pond": "青溪浅滩",
            "fishing_bait": "凡饵",
            "fishing_daily_limit": 20,
            "fishing_daily_day": "",
            "fishing_daily_count": 1,
            "fishing_phase": "opening",
            "fishing_pending_open_fish": "银须灵鲢",
        }
        effect = fishing_behavior.decide_scheduler(snapshot, 1_700_000_000.0)

        self.assertTrue(effect.handled)
        self.assertEqual(".钓鱼 青溪浅滩 凡饵", effect.command)

    def test_fishing_behavior_open_timeout_preserves_pending_queue(self):
        from model.features import fishing_behavior

        now = 1_700_000_000.0
        snapshot = {
            "fishing_enabled": True,
            "fishing_phase": "opening",
            "fishing_reply_to_msg_id": 22042,
            "fishing_reply_due_at": now - 1,
            "next_fishing_time": now - 1,
            "fishing_pending_open_fish": "银须灵鲢",
        }
        effect = fishing_behavior.decide_scheduler(snapshot, now)

        self.assertTrue(effect.handled)
        self.assertEqual("", effect.command)
        self.assertEqual("idle", effect.updates["fishing_phase"])
        self.assertEqual(0, effect.updates["fishing_reply_to_msg_id"])
        self.assertNotIn("fishing_pending_open_fish", effect.updates)
        self.assertEqual(now + fishing_behavior.FISHING_BLOCKED_RETRY_SEC, effect.updates["next_fishing_time"])

    def test_fishing_behavior_open_send_success_waits_for_settlement(self):
        from model.features import fishing_behavior

        now = 1_700_000_000.0
        effect = fishing_behavior.build_send_success_effect(
            {"fishing_started_at": now - 60, "fishing_pending_open_fish": "银须灵鲢"},
            ".开鱼 银须灵鲢",
            sent_at=now,
            msg_id=22042,
            reply_timeout_sec=90,
        )

        self.assertTrue(effect.handled)
        self.assertEqual("opening", effect.updates["fishing_phase"])
        self.assertEqual(22042, effect.updates["fishing_reply_to_msg_id"])
        self.assertEqual(now + 90, effect.updates["fishing_reply_due_at"])
        self.assertEqual("银须灵鲢", effect.updates["fishing_pending_open_fish"])
        self.assertEqual(now + 90, effect.updates["next_fishing_time"])
        self.assertEqual("已发送：.开鱼 银须灵鲢", effect.updates["fishing_last_result"])

    def test_fishing_behavior_open_send_failure_does_not_block_next_rod(self):
        from model.features import fishing_behavior

        now = 1_700_000_000.0
        effect = fishing_behavior.build_send_failure_effect(".开鱼 银须灵鲢", now)

        self.assertTrue(effect.handled)
        self.assertEqual("idle", effect.updates["fishing_phase"])
        self.assertEqual(0, effect.updates["fishing_reply_to_msg_id"])
        self.assertNotIn("fishing_pending_open_fish", effect.updates)
        self.assertEqual(now + fishing_behavior.FISHING_BLOCKED_RETRY_SEC, effect.updates["next_fishing_time"])
        self.assertIn("保留待开队列", effect.updates["fishing_last_error"])

    def test_fishing_behavior_reply_in_progress_checks_status_not_new_rod(self):
        from model.features import fishing_behavior

        effect = fishing_behavior.decide_reply(
            {"fishing_enabled": True},
            "你已有一竿尚未收起。可用 .钓鱼状态 查看，或 .收竿 放弃。",
            1_700_000_000.0,
            result_msg_id=22034,
        )

        self.assertTrue(effect.handled)
        self.assertEqual((), effect.immediate_commands)
        self.assertEqual(".钓鱼状态", effect.updates["fishing_pending_action"])
        self.assertGreater(effect.updates["next_fishing_time"], 1_700_000_000.0)

    def test_fishing_behavior_no_active_fishing_reply_releases_chain(self):
        from model.features import fishing_behavior

        effect = fishing_behavior.decide_reply(
            {"fishing_enabled": True, "fishing_phase": "checking"},
            "你当前没有正在进行的垂钓。",
            1_700_000_000.0,
            result_msg_id=22035,
            post_rod_delay_sec=30,
        )

        self.assertTrue(effect.handled)
        self.assertEqual("idle", effect.updates["fishing_phase"])
        self.assertEqual("", effect.updates["fishing_pending_action"])
        self.assertEqual("当前没有正在进行的垂钓", effect.updates["fishing_last_result"])

    def test_fishing_behavior_reply_returns_patch_and_storage_delta(self):
        from model.features import fishing_behavior

        snapshot = {
            "fishing_enabled": True,
            "fishing_phase": "fishing",
            "fishing_daily_limit": 20,
            "fishing_daily_day": "",
            "fishing_daily_count": 0,
        }
        effect = fishing_behavior.decide_reply(
            snapshot,
            FISHING_START_TEXT,
            1_700_000_000.0,
            result_msg_id=22030,
            action_delay_sec=3,
            post_rod_delay_sec=40,
        )

        self.assertTrue(effect.handled)
        self.assertEqual("waiting", effect.updates["fishing_phase"])
        self.assertEqual(0, effect.updates["fishing_reply_to_msg_id"])
        self.assertEqual(".钓鱼状态", effect.updates["fishing_pending_action"])
        self.assertEqual(1, effect.updates["fishing_daily_count"])
        self.assertEqual({"灵米饵": -1}, effect.storage_deltas)

    def test_fishing_behavior_buy_bait_updates_resource_deltas(self):
        from model.features import fishing_behavior

        effect = fishing_behavior.decide_reply(
            {"fishing_enabled": True},
            "【渔具铺】\n你购得 【妖血饵】x2。",
            1_700_000_000.0,
            result_msg_id=22031,
        )

        self.assertTrue(effect.handled)
        self.assertEqual({"妖血饵": 2, "灵石": -440, "一阶妖丹": -2}, effect.storage_deltas)

    def test_fishing_behavior_basket_calibrates_baits_fish_and_chum(self):
        from model.features import fishing_behavior

        effect = fishing_behavior.decide_reply(
            {"fishing_enabled": True, "fishing_pending_open_fish": '{"旧鱼": 1}'},
            "【鱼篓】\n"
            "青竹钓竿：已持有\n"
            "钓术：Lv.1 垂纶（111熟练度）\n"
            "今日竿数：9/20\n"
            "当前窝料：灵草窝（剩余 4 竿）\n\n"
            "鱼饵\n"
            "- 灵米饵 x3\n\n"
            "鱼获\n"
            "- 银须灵鲢 x2\n\n"
            "可用 .开鱼 <鱼名> [数量] 查看鱼腹机缘。",
            1_700_000_000.0,
            result_msg_id=22039,
        )

        self.assertTrue(effect.handled)
        self.assertEqual(9, effect.updates["fishing_daily_count"])
        self.assertEqual(20, effect.updates["fishing_daily_limit"])
        self.assertEqual("灵草窝", effect.updates["fishing_active_chum_name"])
        self.assertEqual(4, effect.updates["fishing_chum_rods_remaining"])
        self.assertEqual('{"银须灵鲢": 2}', effect.updates["fishing_pending_open_fish"])
        self.assertEqual(3, effect.storage_counts["灵米饵"])
        self.assertEqual(0, effect.storage_counts["凡饵"])
        self.assertEqual(2, effect.storage_counts["银须灵鲢"])

    def test_fishing_behavior_chum_success_updates_full_resource_deltas(self):
        from model.features import fishing_behavior

        effect = fishing_behavior.decide_reply(
            {"fishing_enabled": True, "fishing_chum_day": "", "fishing_chum_counts": ""},
            "【打窝已成】\n你在乱星海礁边撒下 【妖腥窝】，接下来 6 竿会受其牵引。",
            1_700_000_000.0,
            result_msg_id=22032,
        )

        self.assertTrue(effect.handled)
        self.assertEqual({"妖血饵": -2, "一阶妖丹": -3, "灵石": -200}, effect.storage_deltas)
        self.assertEqual(fishing_behavior.get_day_key(1_700_000_000.0), effect.updates["fishing_chum_day"])
        self.assertEqual('{"妖腥窝": 1}', effect.updates["fishing_chum_counts"])

    def test_fishing_behavior_bite_schedules_lift_after_delay(self):
        from model.features import fishing_behavior

        effect = fishing_behavior.decide_reply(
            {"fishing_enabled": True, "fishing_auto_probe_enabled": False},
            FISHING_BITE_TEXT,
            1_700_000_000.0,
            result_msg_id=22031,
        )

        self.assertTrue(effect.handled)
        self.assertEqual((), effect.immediate_commands)
        self.assertEqual(".提竿", effect.updates["fishing_pending_action"])
        self.assertGreater(effect.updates["next_fishing_time"], 1_700_000_000.0)

    def test_fishing_behavior_catch_queues_fish_without_immediate_open(self):
        from model.features import fishing_behavior

        effect = fishing_behavior.decide_reply(
            {"fishing_enabled": True},
            FISHING_CATCH_TEXT,
            1_700_000_000.0,
            result_msg_id=22032,
        )

        self.assertTrue(effect.handled)
        self.assertEqual((), effect.immediate_commands)
        self.assertEqual('{"银须灵鲢": 1}', effect.updates["fishing_pending_open_fish"])
        self.assertGreater(effect.updates["next_fishing_time"], 1_700_000_000.0)

    def test_fishing_behavior_empty_rod_is_terminal_without_opening(self):
        from model.features import fishing_behavior

        effect = fishing_behavior.decide_reply(
            {"fishing_enabled": True, "fishing_phase": "lifting"},
            "【空竿】\n浮漂猛地一沉，又迅速归于平静。你迟疑片刻，那鱼已叼饵而去。\n\n钓术：Lv.0 凡竿 (+1)\n连续空军：1",
            1_700_000_000.0,
            result_msg_id=22036,
            post_rod_delay_sec=30,
        )

        self.assertTrue(effect.handled)
        self.assertEqual((), effect.immediate_commands)
        self.assertEqual("idle", effect.updates["fishing_phase"])
        self.assertNotIn("fishing_pending_open_fish", effect.updates)
        self.assertEqual(1_700_000_030.0, effect.updates["next_fishing_time"])

    def test_fishing_behavior_open_reply_does_not_block_next_rod(self):
        from model.features import fishing_behavior

        now = 1_700_000_000.0
        effect = fishing_behavior.decide_reply(
            {"fishing_enabled": True, "fishing_phase": "idle"},
            OPEN_FISH_TEXT,
            now,
            result_msg_id=22037,
            post_rod_delay_sec=30,
        )

        self.assertTrue(effect.handled)
        self.assertEqual("idle", effect.updates["fishing_phase"])
        self.assertEqual(now, effect.updates["next_fishing_time"])
        self.assertEqual({"银须灵鲢": -1, "灵石": 28, "灵鱼肉": 1, "灵鱼鳞": 1, "清灵草": 1}, effect.storage_deltas)

    def test_fishing_behavior_late_open_reply_preserves_active_rod(self):
        from model.features import fishing_behavior

        now = 1_700_000_000.0
        effect = fishing_behavior.decide_reply(
            {
                "fishing_enabled": True,
                "fishing_phase": "waiting",
                "fishing_reply_to_msg_id": 22050,
                "fishing_reply_due_at": now + 30,
                "fishing_pending_action": ".钓鱼状态",
                "next_fishing_time": now + 20,
            },
            OPEN_FISH_TEXT,
            now,
            result_msg_id=22051,
            post_rod_delay_sec=30,
        )

        self.assertTrue(effect.handled)
        self.assertNotIn("fishing_phase", effect.updates)
        self.assertNotIn("fishing_reply_to_msg_id", effect.updates)
        self.assertNotIn("next_fishing_time", effect.updates)
        self.assertEqual("", effect.updates["fishing_pending_open_fish"])

    def test_fishing_behavior_daily_limit_text_waits_until_next_day(self):
        from model.features import fishing_behavior

        now = 1_700_000_000.0
        effect = fishing_behavior.decide_reply(
            {"fishing_enabled": True},
            "你今日已垂钓 20/20 竿，神识已乏，明日再来。",
            now,
            result_msg_id=22033,
            action_delay_sec=300,
        )

        self.assertTrue(effect.handled)
        self.assertEqual(20, effect.updates["fishing_daily_count"])
        self.assertEqual(20, effect.updates["fishing_daily_limit"])
        self.assertGreater(effect.updates["next_fishing_time"], now + 3600)

    def test_fishing_behavior_daily_limit_text_opens_pending_queue_first(self):
        from model.features import fishing_behavior

        now = 1_700_000_000.0
        effect = fishing_behavior.decide_reply(
            {"fishing_enabled": True, "fishing_pending_open_fish": '{"银须灵鲢": 2}'},
            "你今日已垂钓 20/20 竿，神识已乏，明日再来。",
            now,
            result_msg_id=22033,
            action_delay_sec=8,
        )

        self.assertTrue(effect.handled)
        self.assertEqual(".开鱼 银须灵鲢 2", effect.updates["fishing_pending_action"])
        self.assertEqual(now + 8, effect.updates["next_fishing_time"])


if __name__ == "__main__":
    unittest.main()
