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
from model.features import hehuan, passive_inbox
from model.real_message_replay import get_real_message_text, iter_real_message_samples


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "real_message_samples.json"


def real_text(sample_id):
    return get_real_message_text(FIXTURE_PATH, sample_id)


class HehuanParserTests(unittest.TestCase):
    def test_warm_success_parses_partner_gains_and_contract(self):
        now = 1_779_968_455.0
        parsed = hehuan.parse_hehuan_text(
            real_text("hehuan.warm_success.basic"),
            now=now,
            family="hehuan_dual",
        )

        self.assertEqual("同参道", parsed["path"])
        self.assertEqual("双修 温养", parsed["action"])
        self.assertEqual("success", parsed["result"])
        self.assertEqual("@wuwenyao", parsed["partner"])
        self.assertEqual({"@wushanxiang": 36, "@wuwenyao": 68}, parsed["last_gains"])
        self.assertEqual(15, parsed["last_contrib_gain"])
        self.assertEqual(now + hehuan.HEHUAN_CONTRACT_SEC, parsed["contract_until"])
        self.assertGreater(parsed["next_hehuan_time"], now)

    def test_dual_cooldown_parses_target_and_result(self):
        parsed = hehuan.parse_hehuan_text(
            real_text("hehuan.dual.cooldown"),
            now=1_779_970_000.0,
            family="hehuan_dual",
        )

        self.assertEqual("同参道", parsed["path"])
        self.assertEqual("cooldown", parsed["result"])
        self.assertEqual("@iceeet1", parsed["target"])
        self.assertEqual("心神尚未恢复", parsed["error"])

    def test_invalid_mortal_and_furnace_texts_parse(self):
        invalid = hehuan.parse_hehuan_text(
            real_text("hehuan.seal.invalid_mortal"),
            now=1_779_970_000.0,
            family="hehuan_seal",
        )
        challenged = hehuan.parse_hehuan_text(
            real_text("hehuan.furnace.challenged"),
            now=1_779_970_000.0,
            family="hehuan_seal",
        )
        controlled = hehuan.parse_hehuan_text(
            real_text("hehuan.furnace.controlled"),
            now=1_779_970_000.0,
            family="hehuan_seal",
        )

        self.assertEqual("invalid_target", invalid["result"])
        self.assertEqual("对方只是凡人", invalid["error"])
        self.assertEqual("challenged", challenged["result"])
        self.assertEqual("controlled", controlled["result"])
        self.assertGreater(controlled["heart_seal_until"], 1_779_970_000.0)

    def test_retreat_success_parses_hehuan_bonus(self):
        parsed = hehuan.parse_hehuan_text(
            real_text("hehuan.retreat.success_bonus"),
            now=1_779_970_000.0,
            family="hehuan_retreat",
        )

        self.assertEqual("凡尘缘", parsed["path"])
        self.assertEqual("闭关双修", parsed["action"])
        self.assertEqual({"基础": 132, "合欢宗加成": 66, "最终": 198}, parsed["last_gains"])
        self.assertGreater(parsed["next_hehuan_time"], 1_779_970_000.0)

    def test_guide_and_realm_blocked_parse(self):
        guide = hehuan.parse_hehuan_text(
            real_text("hehuan.guide.basic"),
            now=1_779_970_000.0,
            family="hehuan_dual",
        )
        blocked = hehuan.parse_hehuan_text(
            real_text("hehuan.dual.realm_blocked"),
            now=1_779_970_000.0,
            family="hehuan_dual",
        )

        self.assertEqual("guide", guide["result"])
        self.assertEqual("realm_blocked", blocked["result"])
        self.assertEqual("双方或其中一方尚未踏入仙途", blocked["error"])

    def test_contract_invalid_clears_contract_hint(self):
        now = 1_780_391_144.0
        parsed = hehuan.parse_hehuan_text(
            real_text("hehuan.dual.contract_invalid"),
            now=now,
            family="hehuan_dual",
        )

        self.assertEqual("同参道", parsed["path"])
        self.assertEqual("contract_invalid", parsed["result"])
        self.assertEqual("对方并非你的同参道侣", parsed["error"])
        self.assertEqual(-1, parsed["contract_until"])
        self.assertGreater(parsed["next_hehuan_time"], now)

    def test_concubine_dream_luding_text_is_not_claimed_without_hehuan_family(self):
        text = "【入梦成功】\n*她轻声道：“主人，这炉鼎…可还合用？”*"

        self.assertFalse(hehuan.looks_like_hehuan_text(text))
        self.assertIsNone(hehuan.parse_hehuan_text(text, now=1_779_970_000.0, family=""))


class HehuanManualPlanTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        self.identity_id = 1101
        state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(
            self.identity_id,
            username="hehuan_manual",
            label="hehuan_manual",
            sect_name="合欢宗",
        )

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_warm_plan_requires_recent_contract_hint_and_clear_cooldown(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 60,
                "contract_until": now + 3600,
                "next_hehuan_time": 0,
                "last_partner": "@dao_partner",
            }

            plan = hehuan.build_hehuan_manual_plan("warm", now=now)

        self.assertTrue(plan["allowed"])
        self.assertEqual(".双修 温养", plan["command"])
        self.assertEqual("hehuan_dual", plan["family"])
        self.assertEqual(0, plan["max_retry"])

    def test_warm_plan_blocks_without_contract_or_during_cooldown(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 60,
                "contract_until": 0,
                "next_hehuan_time": 0,
                "last_partner": "",
            }
            no_contract = hehuan.build_hehuan_manual_plan("warm", now=now)

            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 60,
                "contract_until": now + 3600,
                "next_hehuan_time": now + 600,
                "last_partner": "@dao_partner",
            }
            cooldown = hehuan.build_hehuan_manual_plan("warm", now=now)

        self.assertFalse(no_contract["allowed"])
        self.assertIn("未确认有效同参契印", no_contract["reason"])
        self.assertFalse(cooldown["allowed"])
        self.assertIn("冷却", cooldown["reason"])


class HehuanSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        self.identity_id = 1102
        state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(
            self.identity_id,
            username="hehuan_auto",
            label="hehuan_auto",
            sect_name="合欢宗",
        )

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    async def test_scheduler_sends_warm_when_contract_hint_and_due(self):
        now = 1_780_000_000.0
        msg = SimpleNamespace(id=9001, sent_at=now)
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 60,
                "contract_until": now + 3600,
                "next_hehuan_time": 0,
                "last_partner": "@dao_partner",
                "auto_next_time": now - 1,
            }
            with patch.object(hehuan, "save_state"), patch.object(hehuan, "send_game_command", return_value=msg) as send_mock:
                await hehuan.run_hehuan_scheduler(now)

            send_mock.assert_awaited_once()
            self.assertEqual(".双修 温养", send_mock.await_args.args[0])
            self.assertEqual("合欢宗", send_mock.await_args.kwargs["source_module"])
            self.assertEqual(0, send_mock.await_args.kwargs["max_retry"])
            observed = state_module.state["hehuan_observation"]
            self.assertEqual("warm", observed["auto_last_action"])
            self.assertEqual("", observed["auto_last_error"])
            self.assertGreater(observed["auto_next_time"], now)

    async def test_scheduler_respects_future_auto_time(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 60,
                "contract_until": now + 3600,
                "next_hehuan_time": 0,
                "last_partner": "@dao_partner",
                "auto_next_time": now + 300,
            }
            with patch.object(hehuan, "send_game_command") as send_mock:
                await hehuan.run_hehuan_scheduler(now)

            send_mock.assert_not_called()

    async def test_scheduler_blocks_without_contract_hint_and_backs_off(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 60,
                "contract_until": 0,
                "next_hehuan_time": 0,
                "last_partner": "",
                "auto_next_time": now - 1,
            }
            with patch.object(hehuan, "save_state"), patch.object(hehuan, "send_game_command") as send_mock:
                await hehuan.run_hehuan_scheduler(now)

            send_mock.assert_not_called()
            observed = state_module.state["hehuan_observation"]
            self.assertEqual("warm", observed["auto_last_action"])
            self.assertIn("未确认有效同参契印", observed["auto_last_error"])
            self.assertGreaterEqual(observed["auto_next_time"], now + hehuan.HEHUAN_AUTO_BLOCK_BACKOFF_SEC)


class HehuanPassiveInboxTests(unittest.TestCase):
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

    def _prepare_identity(self, send_as_id=1001, username="wushanxiang"):
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(
            send_as_id,
            username=username,
            label=username,
            sect_name="合欢宗",
        )
        with state_module.use_identity(send_as_id):
            state_module.state["hehuan_enabled"] = True
        return send_as_id

    def test_passive_inbox_updates_hehuan_observation_from_reply_context(self):
        send_as_id = self._prepare_identity()
        event = SimpleNamespace(chat_id=-1001680975844, id=9605457)

        with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
            handled = asyncio.run(passive_inbox.handle_passive_module_card(
                real_text("hehuan.warm_success.basic"),
                now=1_779_968_455.0,
                reply_context={
                    "send_as_id": send_as_id,
                    "family": "hehuan_dual",
                    "reply_to_msg_id": 9605454,
                    "root_msg_id": 9605454,
                },
                event=event,
                event_type="message",
            ))

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            observed = state_module.state["hehuan_observation"]
            self.assertEqual("同参道", observed["last_path"])
            self.assertEqual("双修 温养", observed["last_action"])
            self.assertEqual("success", observed["last_result"])
            self.assertEqual("@wuwenyao", observed["last_partner"])
            self.assertEqual(15, observed["last_contrib_gain"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["changed"])
        self.assertEqual(1, snapshot["modules"]["hehuan"])

    def test_passive_inbox_can_route_at_tagged_hehuan_text_without_reply_context(self):
        send_as_id = self._prepare_identity(username="iceeet1")
        event = SimpleNamespace(chat_id=-1001680975844, id=9607365)

        with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
            handled = asyncio.run(passive_inbox.handle_passive_module_card(
                real_text("hehuan.dual.cooldown"),
                now=1_779_970_000.0,
                reply_context=None,
                event=event,
                event_type="message",
            ))

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            observed = state_module.state["hehuan_observation"]
            self.assertEqual("cooldown", observed["last_result"])
            self.assertEqual("@iceeet1", observed["last_target"])

    def test_passive_inbox_clears_contract_on_invalid_partner_reply(self):
        send_as_id = self._prepare_identity(username="iceeet1")
        event = SimpleNamespace(chat_id=-1001680975844, id=9733738)
        with state_module.use_identity(send_as_id):
            state_module.state["hehuan_observation"] = {
                "last_observed_at": 1_780_390_000.0,
                "contract_until": 1_780_999_999.0,
                "next_hehuan_time": 0,
                "auto_next_time": 1_780_390_000.0,
            }

        with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
            handled = asyncio.run(passive_inbox.handle_passive_module_card(
                real_text("hehuan.dual.contract_invalid"),
                now=1_780_391_144.0,
                reply_context={
                    "send_as_id": send_as_id,
                    "family": "hehuan_dual",
                    "reply_to_msg_id": 9733737,
                    "root_msg_id": 9733737,
                },
                event=event,
                event_type="message",
            ))

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            observed = state_module.state["hehuan_observation"]
            self.assertEqual("contract_invalid", observed["last_result"])
            self.assertEqual(0, observed["contract_until"])
            self.assertGreater(observed["auto_next_time"], 1_780_391_144.0)

    def test_real_message_fixture_includes_hehuan_samples(self):
        samples = list(iter_real_message_samples(FIXTURE_PATH, module="hehuan"))

        self.assertGreaterEqual(len(samples), 7)
        self.assertTrue(all(sample.family.startswith("hehuan_") for sample in samples))


if __name__ == "__main__":
    unittest.main()
