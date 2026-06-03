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
from model.features import passive_inbox, tianxing
from model.real_message_replay import get_real_message_text, iter_real_message_samples


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "real_message_samples.json"


def real_text(sample_id):
    return get_real_message_text(FIXTURE_PATH, sample_id)


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
        self.assertEqual("", parsed["fixed_star"])
        self.assertEqual(63, parsed["tianji_value"])
        self.assertEqual(2, parsed["calamity_count"])
        self.assertEqual((189, 6, 40), (parsed["hit_count"], parsed["miss_count"], parsed["change_count"]))

    def test_observe_predict_change_and_modifier_parse(self):
        observe = tianxing.parse_tianxing_text(real_text("tianxing.observe.basic"), now=1_780_000_000.0)
        predict = tianxing.parse_tianxing_text(real_text("tianxing.predict.basic"), now=1_780_000_000.0)
        change = tianxing.parse_tianxing_text(real_text("tianxing.change_fate.basic"), now=1_780_000_000.0)
        modifier = tianxing.parse_tianxing_text(real_text("tianxing.modifier.wild"), now=1_780_000_000.0)

        self.assertEqual(["天府", "太阴", "贪狼"], observe["available_stars"])
        self.assertEqual("炼制", predict["current_prediction"])
        self.assertGreater(predict["current_prediction_until"], 1_780_000_000.0)
        self.assertEqual("探索", change["current_change"])
        self.assertGreater(change["current_change_until"], 1_780_000_000.0)
        self.assertEqual("prediction_hit", modifier["result"])
        self.assertEqual(1, modifier["last_tianji_gain"])
        self.assertEqual(30, modifier["last_contrib_gain"])
        self.assertIn("太阴", modifier["last_star_effect"])

    def test_clear_calamity_and_join_blocked_parse(self):
        clear = tianxing.parse_tianxing_text(real_text("tianxing.clear_calamity.basic"), now=1_780_000_000.0)
        blocked = tianxing.parse_tianxing_text(real_text("tianxing.join.not_qualified"), now=1_780_000_000.0)

        self.assertEqual("消劫", clear["action"])
        self.assertEqual("success", clear["result"])
        self.assertEqual("not_qualified", blocked["result"])
        self.assertEqual("无法感应九天星辰之力", blocked["last_error"])


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

    def test_set_star_requires_recent_available_star_and_no_existing_fixed_star(self):
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
            fixed = tianxing.build_tianxing_manual_plan("set_star", "天府", now=now)

        self.assertTrue(allowed["allowed"])
        self.assertEqual(".定命 天府", allowed["command"])
        self.assertFalse(unavailable["allowed"])
        self.assertIn("今日可选命星", unavailable["reason"])
        self.assertFalse(fixed["allowed"])
        self.assertIn("今日已定命星", fixed["reason"])

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
            state_module.state["tianxing_observation"]["current_change_until"] = now + 600
            change_cooldown = tianxing.build_tianxing_manual_plan("change_fate", "探索", now=now)

            state_module.state["tianxing_observation"]["current_change_until"] = 0
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
        self.assertFalse(change_cooldown["allowed"])
        self.assertIn("已有改命", change_cooldown["reason"])
        self.assertFalse(tianji_shortage["allowed"])
        self.assertIn("天机值不足", tianji_shortage["reason"])
        self.assertFalse(no_calamity["allowed"])
        self.assertIn("未记录逆命劫", no_calamity["reason"])


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

    async def _run_with_observation(self, observation, now=1_780_000_000.0):
        msg = SimpleNamespace(id=9101, sent_at=now)
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = observation
            with patch.object(tianxing, "save_state"), patch.object(tianxing, "send_game_command", return_value=msg) as send_mock:
                await tianxing.run_tianxing_scheduler(now)
            return send_mock, state_module.state["tianxing_observation"]

    async def test_scheduler_queries_panel_when_observation_missing_or_stale(self):
        send_mock, observed = await self._run_with_observation({})

        send_mock.assert_awaited_once()
        self.assertEqual(".天机盘", send_mock.await_args.args[0])
        self.assertEqual("天星宗", send_mock.await_args.kwargs["source_module"])
        self.assertEqual("panel", observed["auto_last_action"])

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

        send_mock.assert_not_called()
        self.assertEqual("idle", observed["auto_last_action"])

    async def test_scheduler_respects_future_auto_time(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["tianxing_enabled"] = True
            state_module.state["tianxing_observation"] = {
                "last_observed_at": now - 60,
                "available_stars": [],
                "fixed_star": "",
                "auto_next_time": now + 300,
            }
            with patch.object(tianxing, "send_game_command") as send_mock:
                await tianxing.run_tianxing_scheduler(now)

            send_mock.assert_not_called()


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

    def test_real_message_fixture_includes_tianxing_samples(self):
        samples = list(iter_real_message_samples(FIXTURE_PATH, module="tianxing"))

        self.assertGreaterEqual(len(samples), 7)
        self.assertTrue(all(sample.family.startswith("tianxing_") for sample in samples))


if __name__ == "__main__":
    unittest.main()
