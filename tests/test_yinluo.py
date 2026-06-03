import asyncio
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import passive_inbox, yinluo
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
        self.assertEqual(1, parsed["refining_slots"])
        self.assertEqual(191, parsed["soul_stocks"]["妖兽精魄"])

    def test_demon_summon_cooldown_convert_and_retreat_parse(self):
        now = 1_779_450_000.0
        success = yinluo.parse_yinluo_text(real_text("yinluo.demon_summon.success"), now=1_779_450_000.0)
        cooldown = yinluo.parse_yinluo_text(real_text("yinluo.demon_summon.cooldown"), now=1_779_450_000.0)
        realm_blocked = yinluo.parse_yinluo_text(real_text("yinluo.demon_summon.realm_blocked"), now=now)
        convert = yinluo.parse_yinluo_text(real_text("yinluo.convert.success"), now=1_779_450_000.0)
        retreat = yinluo.parse_yinluo_text(real_text("yinluo.retreat.success_bonus"), now=1_779_450_000.0)

        self.assertEqual("召唤魔影", success["action"])
        self.assertEqual("success", success["result"])
        self.assertEqual("凶兽戾魄", success["last_resource"])
        self.assertEqual("cooldown", cooldown["result"])
        self.assertGreater(cooldown["next_demon_summon_time"], 1_779_450_000.0)
        self.assertEqual("召唤魔影", realm_blocked["action"])
        self.assertEqual("realm_blocked", realm_blocked["result"])
        self.assertEqual("境界尚未达到结丹期", realm_blocked["last_error"])
        self.assertEqual(2030, convert["last_sha_gain"])
        self.assertEqual(1530, convert["last_extra_sha_gain"])
        self.assertEqual(68, retreat["last_bonus_gain"])

    def test_blood_forest_success_and_cooldown_parse_real_text(self):
        now = 1_779_450_000.0
        success = yinluo.parse_yinluo_text(real_text("yinluo.blood_forest.success"), now=now)
        cooldown = yinluo.parse_yinluo_text(real_text("yinluo.blood_forest.cooldown"), now=now)

        self.assertEqual("血洗山林", success["action"])
        self.assertEqual("success", success["result"])
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
            }
            summon = yinluo.build_yinluo_manual_plan("demon_summon", now=now)
            collect = yinluo.build_yinluo_manual_plan("collect", now=now)
            convert = yinluo.build_yinluo_manual_plan("convert", "1000", now=now)

            state_module.state["yinluo_observation"]["next_demon_summon_time"] = now + 600
            summon_cooldown = yinluo.build_yinluo_manual_plan("demon_summon", now=now)

            state_module.state["yinluo_observation"]["next_demon_summon_time"] = 0
            state_module.state["yinluo_observation"]["ready_slots"] = 0
            collect_empty = yinluo.build_yinluo_manual_plan("collect", now=now)

            convert_missing_amount = yinluo.build_yinluo_manual_plan("convert", "", now=now)
            convert_too_large = yinluo.build_yinluo_manual_plan("convert", "10001", now=now)

        self.assertTrue(summon["allowed"])
        self.assertEqual(".召唤魔影", summon["command"])
        self.assertTrue(collect["allowed"])
        self.assertEqual(".收取幡魂", collect["command"])
        self.assertTrue(convert["allowed"])
        self.assertEqual(".化功为煞 1000", convert["command"])
        self.assertFalse(summon_cooldown["allowed"])
        self.assertIn("冷却", summon_cooldown["reason"])
        self.assertFalse(collect_empty["allowed"])
        self.assertIn("未记录可收取", collect_empty["reason"])
        self.assertFalse(convert_missing_amount["allowed"])
        self.assertIn("必须指定正整数", convert_missing_amount["reason"])
        self.assertFalse(convert_too_large["allowed"])
        self.assertIn("上限", convert_too_large["reason"])

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

            curse = yinluo.build_yinluo_manual_plan("下咒", now=now)
            possess = yinluo.build_yinluo_manual_plan("夺舍", now=now)

        self.assertTrue(due["allowed"])
        self.assertEqual(".血洗山林", due["command"])
        self.assertEqual("yinluo_blood_forest", due["family"])
        self.assertFalse(cooldown["allowed"])
        self.assertIn("冷却", cooldown["reason"])
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

    async def test_scheduler_collects_ready_slots_and_clears_local_ready_hint(self):
        now = 1_780_000_000.0
        send_mock, observed = await self._run_with_observation({
            "last_observed_at": now - 60,
            "banner_owner": "水镜真人",
            "banner_name": "灭法幡",
            "ready_slots": 2,
            "next_blood_forest_time": now + 3600,
            "next_demon_summon_time": now + 3600,
            "auto_next_time": now - 1,
        }, now=now)

        send_mock.assert_awaited_once()
        self.assertEqual(".收取幡魂", send_mock.await_args.args[0])
        self.assertEqual("collect", observed["auto_last_action"])
        self.assertEqual(0, observed["ready_slots"])

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

    def test_real_message_fixture_includes_yinluo_samples(self):
        samples = list(iter_real_message_samples(FIXTURE_PATH, module="yinluo"))

        self.assertGreaterEqual(len(samples), 8)
        self.assertTrue(all(sample.family.startswith("yinluo_") for sample in samples))


if __name__ == "__main__":
    unittest.main()
