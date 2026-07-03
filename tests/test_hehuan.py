import asyncio
import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


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

    def test_warm_success_parses_valuable_insight(self):
        now = 1_779_968_455.0
        text = (
            "【温养双修·圆满】\n"
            "在同参契印的加持下，你与 @wuwenyao 灵力交融。\n"
            "@wushanxiang 修为增加了 36 点，并获得 15 点宗门贡献！\n"
            "@wuwenyao 修为增加了 68 点！\n"
            "共同领悟了【青元剑诀】。"
        )
        parsed = hehuan.parse_hehuan_text(text, now=now, family="hehuan_dual")

        self.assertEqual("success", parsed["result"])
        self.assertEqual("青元剑诀", parsed["last_insight"])

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

    def test_contract_not_member_is_observed_without_new_runtime_branch(self):
        parsed = hehuan.parse_hehuan_text(
            real_text("hehuan.contract.not_member"),
            now=1_780_000_000.0,
            family="hehuan_contract",
        )

        self.assertEqual("未知合欢宗文案", parsed["action"])
        self.assertEqual("observed", parsed["result"])
        self.assertIn("并非合欢宗弟子", parsed["summary"])

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

            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 60,
                "contract_until": now + 3600,
                "next_hehuan_time": 0,
                "last_result": "cooldown",
                "last_partner": "@dao_partner",
            }
            unknown_cooldown = hehuan.build_hehuan_manual_plan("warm", now=now)

        self.assertFalse(no_contract["allowed"])
        self.assertIn("未确认有效同参契印", no_contract["reason"])
        self.assertFalse(cooldown["allowed"])
        self.assertIn("冷却", cooldown["reason"])
        self.assertFalse(unknown_cooldown["allowed"])
        self.assertIn("冷却时间不可解析", unknown_cooldown["reason"])

    def test_warm_plan_blocks_dirty_time_fields_without_guessing(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 60,
                "contract_until": "inf",
                "next_hehuan_time": 0,
                "last_partner": "@dao_partner",
            }

            plan = hehuan.build_hehuan_manual_plan("warm", now=now)

        self.assertFalse(plan["allowed"])
        self.assertIn("状态字段异常", plan["reason"])

    def test_status_text_tolerates_dirty_time_fields(self):
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": "观测时间异常",
                "contract_until": "inf",
                "next_hehuan_time": "nan",
                "heart_seal_until": "-inf",
                "auto_next_time": "自动时间异常",
                "recent": [{"ts": "inf", "path": "同参道", "action": "双修 温养", "result": "pending"}],
            }

            text = hehuan.get_hehuan_status_text()

        self.assertIn("🌸 合欢宗", text)
        self.assertIn("状态异常", text)
        self.assertIn("未设置", text)

    def test_success_records_last_warm_success_and_clears_retry(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "auto_retry_count": 3,
                "auto_retry_reason": "温养回复超时或被吞",
                "auto_pending_msg_id": 123,
                "auto_pending_sent_at": now - 90,
                "auto_pending_deadline_at": now - 1,
            }

            changed = hehuan.apply_hehuan_passive(
                real_text("hehuan.warm_success.basic"),
                now=now,
                family="hehuan_dual",
            )

            observed = state_module.state["hehuan_observation"]

        self.assertTrue(changed)
        self.assertEqual(now, observed["last_warm_success_at"])
        self.assertEqual(now + hehuan.HEHUAN_WARM_OBSERVED_CD_SEC, observed["next_hehuan_time"])
        self.assertEqual(0, observed["auto_retry_count"])
        self.assertEqual(0, observed["auto_pending_msg_id"])
        self.assertEqual([], observed["valuable_drop_reminders"])

    def test_success_with_valuable_insight_queues_three_reminders(self):
        now = 1_780_000_000.0
        text = (
            "【温养双修·圆满】\n"
            "在同参契印的加持下，你与 @wuwenyao 灵力交融。\n"
            "@wushanxiang 修为增加了 36 点，并获得 15 点宗门贡献！\n"
            "@wuwenyao 修为增加了 68 点！\n"
            "共同领悟了【青元剑诀】。"
        )
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            changed = hehuan.apply_hehuan_passive(text, now=now, family="hehuan_dual")
            observed = state_module.state["hehuan_observation"]

        self.assertTrue(changed)
        self.assertEqual(1, len(observed["valuable_drop_reminders"]))
        reminder = observed["valuable_drop_reminders"][0]
        self.assertEqual("青元剑诀", reminder["item"])
        self.assertEqual("@wuwenyao", reminder["partner"])
        self.assertEqual(0, reminder["next_index"])
        self.assertEqual(now, reminder["next_reminder_at"])

    def test_cooldown_without_success_time_blocks_for_one_hour(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 30,
                "contract_until": now + 3600,
                "auto_retry_max_interval_min": 5,
            }
            with patch.object(hehuan.random, "uniform", return_value=120):
                changed = hehuan.apply_hehuan_passive(
                    real_text("hehuan.dual.cooldown"),
                    now=now,
                    family="hehuan_dual",
                )
            observed = state_module.state["hehuan_observation"]

        self.assertTrue(changed)
        self.assertEqual(0, observed["auto_retry_count"])
        self.assertEqual("", observed["auto_retry_reason"])
        self.assertEqual(now + hehuan.HEHUAN_WARM_OBSERVED_CD_SEC, observed["next_hehuan_time"])
        self.assertEqual(observed["next_hehuan_time"], observed["auto_next_time"])
        self.assertIn("1小时冷却", observed["auto_last_error"])

    def test_cooldown_with_success_time_uses_success_plus_one_hour(self):
        now = 1_780_000_000.0
        last_success = now - 1800
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_warm_success_at": last_success,
                "contract_until": now + 3600,
                "auto_retry_count": 0,
            }
            changed = hehuan.apply_hehuan_passive(
                real_text("hehuan.dual.cooldown"),
                now=now,
                family="hehuan_dual",
            )
            observed = state_module.state["hehuan_observation"]

        self.assertTrue(changed)
        self.assertEqual(last_success + hehuan.HEHUAN_WARM_OBSERVED_CD_SEC, observed["next_hehuan_time"])
        self.assertEqual(observed["next_hehuan_time"], observed["auto_next_time"])
        self.assertEqual(0, observed["auto_retry_count"])

    def test_cooldown_with_stale_success_time_blocks_for_one_hour(self):
        now = 1_780_000_000.0
        last_success = now - hehuan.HEHUAN_WARM_OBSERVED_CD_SEC - 300
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_warm_success_at": last_success,
                "contract_until": now + 3600,
                "auto_retry_count": hehuan.HEHUAN_AUTO_RETRY_LIMIT,
                "auto_retry_reason": "温养回复超时或被吞",
                "auto_pending_msg_id": 9901,
                "auto_pending_sent_at": now - 300,
                "auto_pending_deadline_at": now - 1,
            }
            changed = hehuan.apply_hehuan_passive(
                real_text("hehuan.dual.cooldown"),
                now=now,
                family="hehuan_dual",
            )
            observed = state_module.state["hehuan_observation"]

        self.assertTrue(changed)
        self.assertEqual(now + hehuan.HEHUAN_WARM_OBSERVED_CD_SEC, observed["next_hehuan_time"])
        self.assertEqual(observed["next_hehuan_time"], observed["auto_next_time"])
        self.assertEqual(0, observed["auto_retry_count"])
        self.assertEqual("", observed["auto_retry_reason"])
        self.assertEqual(0, observed["auto_pending_msg_id"])
        self.assertNotIn("补发已达", observed["auto_last_error"])


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
            with (
                patch.object(hehuan, "save_state"),
                patch.object(hehuan, "_ensure_hehuan_reply_anchor", new=AsyncMock(return_value=(8801, ""))),
                patch.object(hehuan, "_hehuan_retry_delay_sec", return_value=123.0) as delay_mock,
                patch.object(hehuan, "send_game_command", return_value=msg) as send_mock,
            ):
                await hehuan.run_hehuan_scheduler(now)

            send_mock.assert_awaited_once()
            delay_mock.assert_called_once()
            self.assertEqual(".双修 温养", send_mock.await_args.args[0])
            self.assertEqual(8801, send_mock.await_args.kwargs["reply_to"])
            self.assertEqual("合欢宗", send_mock.await_args.kwargs["source_module"])
            self.assertEqual(0, send_mock.await_args.kwargs["max_retry"])
            self.assertEqual(123, send_mock.await_args.kwargs["reply_timeout"])
            observed = state_module.state["hehuan_observation"]
            self.assertEqual("warm", observed["auto_last_action"])
            self.assertEqual("", observed["auto_last_error"])
            self.assertEqual(9001, observed["auto_pending_msg_id"])
            self.assertEqual(8801, observed["auto_reply_anchor_msg_id"])
            self.assertEqual(now + 123.0, observed["auto_pending_deadline_at"])
            self.assertEqual(observed["auto_pending_deadline_at"], observed["auto_next_time"])

    def test_recent_baiji_anchor_skips_command_messages(self):
        base_dt = datetime(2026, 6, 29, 12, 5, tzinfo=hehuan.TZ_LOCAL)
        now = base_dt.timestamp()
        entries = [
            {
                "ts": "2026-06-29 12:03:00 UTC+8",
                "event_type": "message",
                "message_id": 8801,
                "chat_id": -1001680975844,
                "sender_id": hehuan.HEHUAN_BAIJI_SEND_AS_ID,
                "topic_id": 7310786,
                "reply_to_msg_id": 0,
                "text": hehuan.HEHUAN_ANCHOR_TEXT,
            },
            {
                "ts": "2026-06-29 12:04:00 UTC+8",
                "event_type": "sent",
                "message_id": 8802,
                "chat_id": -1001680975844,
                "sender_id": hehuan.HEHUAN_BAIJI_SEND_AS_ID,
                "topic_id": 7310786,
                "reply_to_msg_id": 0,
                "text": ".神迹 赈灾",
                "source_module": "小世界",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2026-06-29.log"
            log_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in entries), encoding="utf-8")
            with (
                patch.object(hehuan, "MESSAGES_DIR", tmpdir),
                patch.object(hehuan, "get_game_group_id", return_value=-1001680975844),
                patch.object(hehuan, "get_game_topic_id", return_value=7310786),
            ):
                msg_id = hehuan.find_recent_baiji_anchor_msg_id(now=now)

        self.assertEqual(8801, msg_id)

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

    async def test_scheduler_blocks_dirty_time_fields_without_clearing_or_saving(self):
        now = 1_780_000_000.0
        dirty_cases = (
            ("auto_next_time", "nan"),
            ("next_hehuan_time", "inf"),
            ("contract_until", "契印时间异常"),
            ("last_observed_at", "-inf"),
        )
        for field_name, dirty_value in dirty_cases:
            with self.subTest(field_name=field_name):
                observation = {
                    "last_observed_at": now - 60,
                    "contract_until": now + 3600,
                    "next_hehuan_time": 0,
                    "last_partner": "@dao_partner",
                    "auto_next_time": now - 1,
                }
                observation[field_name] = dirty_value
                with state_module.use_identity(self.identity_id):
                    state_module.state["hehuan_enabled"] = True
                    state_module.state["hehuan_observation"] = observation
                    with (
                        patch.object(hehuan, "save_state") as save_mock,
                        patch.object(hehuan, "send_game_command") as send_mock,
                    ):
                        await hehuan.run_hehuan_scheduler(now)

                    send_mock.assert_not_called()
                    save_mock.assert_not_called()
                    self.assertEqual(dirty_value, state_module.state["hehuan_observation"][field_name])

    async def test_scheduler_blocks_pending_warm_settlement_without_sending(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 60,
                "last_result": "pending",
                "contract_until": now + 3600,
                "next_hehuan_time": 0,
                "last_partner": "@dao_partner",
                "auto_next_time": now - 1,
            }
            with patch.object(hehuan, "save_state") as save_mock, patch.object(hehuan, "send_game_command") as send_mock:
                await hehuan.run_hehuan_scheduler(now)

            send_mock.assert_not_called()
            save_mock.assert_called_once()
            observed = state_module.state["hehuan_observation"]
            self.assertEqual("pending", observed["last_result"])
            self.assertIn("仍待真实结果", observed["auto_last_error"])

    async def test_scheduler_stops_after_retry_limit_on_swallowed_reply(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 600,
                "contract_until": now + 3600,
                "next_hehuan_time": 0,
                "last_partner": "@dao_partner",
                "auto_next_time": now - 1,
                "auto_retry_count": hehuan.HEHUAN_AUTO_RETRY_LIMIT,
                "auto_pending_msg_id": 9901,
                "auto_pending_sent_at": now - 300,
                "auto_pending_deadline_at": now - 1,
            }
            with patch.object(hehuan, "save_state") as save_mock, patch.object(hehuan, "send_game_command") as send_mock:
                await hehuan.run_hehuan_scheduler(now)

            send_mock.assert_not_called()
            save_mock.assert_called_once()
            observed = state_module.state["hehuan_observation"]
            self.assertIn("补发已达", observed["auto_last_error"])
            self.assertEqual(0, observed["auto_pending_msg_id"])

    async def test_scheduler_recovers_pending_warm_reply_from_message_log(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 600,
                "contract_until": now + 3600,
                "next_hehuan_time": 0,
                "last_partner": "@dao_partner",
                "auto_next_time": now - 1,
                "auto_retry_count": 1,
                "auto_pending_msg_id": 9901,
                "auto_pending_sent_at": now - 300,
                "auto_pending_deadline_at": now - 1,
            }
            replies = [{
                "text": real_text("hehuan.warm_success.basic"),
                "ts_epoch": now - 10,
                "message_id": 9902,
                "reply_to_msg_id": 9901,
            }]
            with (
                patch.object(hehuan, "find_message_log_replies", return_value=replies) as recovery_mock,
                patch.object(hehuan, "save_state") as save_mock,
                patch.object(hehuan, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                await hehuan.run_hehuan_scheduler(now)

            recovery_mock.assert_called_once()
            send_mock.assert_not_called()
            save_mock.assert_called_once()
            observed = state_module.state["hehuan_observation"]

        self.assertEqual("success", observed["last_result"])
        self.assertEqual(0, observed["auto_pending_msg_id"])
        self.assertEqual(0, observed["auto_retry_count"])
        self.assertEqual(now - 10 + hehuan.HEHUAN_WARM_OBSERVED_CD_SEC, observed["next_hehuan_time"])

    async def test_scheduler_sends_bare_warm_when_recent_anchor_missing(self):
        now = 1_780_000_000.0
        warm_msg = SimpleNamespace(id=9002, sent_at=now + 1)
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 60,
                "contract_until": now + 3600,
                "next_hehuan_time": 0,
                "last_partner": "@dao_partner",
                "auto_next_time": now - 1,
            }
            with (
                patch.object(hehuan, "save_state"),
                patch.object(hehuan, "find_recent_baiji_anchor_msg_id", return_value=0),
                patch.object(hehuan, "send_game_command", new=AsyncMock(return_value=warm_msg)) as send_mock,
            ):
                await hehuan.run_hehuan_scheduler(now)

            self.assertEqual(1, send_mock.await_count)
            warm_call = send_mock.await_args_list[0]
            self.assertEqual(".双修 温养", warm_call.args[0])
            self.assertNotIn("reply_to", warm_call.kwargs)
            self.assertEqual(0, state_module.state["hehuan_observation"]["auto_reply_anchor_msg_id"])

    async def test_scheduler_sends_valuable_drop_reminders_three_times(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = False
            state_module.state["hehuan_observation"] = {
                "valuable_drop_reminders": [{
                    "event_id": "hehuan-warm:@dao_partner:青元剑诀:29666666",
                    "source": "合欢双修温养",
                    "item": "青元剑诀",
                    "partner": "@dao_partner",
                    "event_at": now,
                    "next_index": 0,
                    "next_reminder_at": now,
                    "done": False,
                }]
            }
            with patch.object(hehuan, "save_state") as save_mock, patch.object(hehuan, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock:
                await hehuan.run_hehuan_scheduler(now)
                await hehuan.run_hehuan_scheduler(now + 60)
                await hehuan.run_hehuan_scheduler(now + 3 * 3600)
                await hehuan.run_hehuan_scheduler(now + 6 * 3600)
            observed = state_module.state["hehuan_observation"]

        self.assertEqual(3, audit_mock.await_count)
        self.assertIn("青元剑诀", audit_mock.await_args_list[0].args[0])
        self.assertIn("第1/3次，即时", audit_mock.await_args_list[0].args[0])
        self.assertIn("第2/3次，+3h", audit_mock.await_args_list[1].args[0])
        self.assertIn("第3/3次，+6h", audit_mock.await_args_list[2].args[0])
        self.assertEqual("high", audit_mock.await_args_list[0].kwargs["priority"])
        self.assertTrue(observed["valuable_drop_reminders"][0]["done"])
        self.assertEqual(3, observed["valuable_drop_reminders"][0]["next_index"])
        self.assertGreaterEqual(save_mock.call_count, 3)

    async def test_scheduler_retries_valuable_drop_reminder_when_audit_send_fails(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = False
            state_module.state["hehuan_observation"] = {
                "valuable_drop_reminders": [{
                    "event_id": "hehuan-warm:@dao_partner:青元剑诀:29666666",
                    "source": "合欢双修温养",
                    "item": "青元剑诀",
                    "partner": "@dao_partner",
                    "event_at": now,
                    "next_index": 0,
                    "next_reminder_at": now,
                    "done": False,
                }]
            }
            with patch.object(hehuan, "save_state"), patch.object(hehuan, "send_audit_log", new=AsyncMock(return_value=False)) as audit_mock:
                await hehuan.run_hehuan_scheduler(now)
            observed = state_module.state["hehuan_observation"]

        self.assertEqual(1, audit_mock.await_count)
        self.assertEqual(0, observed["valuable_drop_reminders"][0]["next_index"])
        self.assertEqual(now + 5 * 60, observed["valuable_drop_reminders"][0]["next_reminder_at"])
        self.assertFalse(observed["valuable_drop_reminders"][0]["done"])

    async def test_scheduler_does_not_send_warm_in_same_tick_as_valuable_reminder(self):
        now = 1_780_000_000.0
        with state_module.use_identity(self.identity_id):
            state_module.state["hehuan_enabled"] = True
            state_module.state["hehuan_observation"] = {
                "last_observed_at": now - 60,
                "contract_until": now + 3600,
                "next_hehuan_time": 0,
                "last_partner": "@dao_partner",
                "auto_next_time": now - 1,
                "valuable_drop_reminders": [{
                    "event_id": "hehuan-warm:@dao_partner:青元剑诀:29666666",
                    "source": "合欢双修温养",
                    "item": "青元剑诀",
                    "partner": "@dao_partner",
                    "event_at": now,
                    "next_index": 0,
                    "next_reminder_at": now,
                    "done": False,
                }]
            }
            with (
                patch.object(hehuan, "save_state"),
                patch.object(hehuan, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(hehuan, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                await hehuan.run_hehuan_scheduler(now)

        audit_mock.assert_awaited_once()
        send_mock.assert_not_awaited()


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
