import atexit
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
CREATED_ENV = False

if not ENV_PATH.exists():
    ENV_PATH.write_text(
        "\n".join(
            [
                "API_ID=12345",
                "API_HASH=00000000000000000000000000000000",
                "TG_PROXY_TYPE=",
                "TG_PROXY_HOST=127.0.0.1:7890",
                "LOG_GROUP_ID=0",
                "LOG_SEND_MODE=account",
                "ADMIN_ID=1",
                "CHAOGU_UI_HOST=127.0.0.1",
                "CHAOGU_UI_PORT=3030",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    CREATED_ENV = True

if CREATED_ENV:
    atexit.register(lambda: ENV_PATH.exists() and ENV_PATH.unlink())

sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import pet, storage_bag


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class PetWarmTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    async def test_pet_touch_scheduler_uses_short_reply_timeout_and_pending_guard(self):
        send_as_id = 8659059188
        now = 5000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, pet_name="青竹蜂云剑（金雷竹·庚金相）")

        with state_module.use_identity(send_as_id):
            state_module.state["pet_enabled"] = True
            state_module.state["next_pet_time"] = now - 1

            with (
                patch.object(pet.random, "uniform", return_value=12),
                patch.object(pet, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7001, sent_at=now + 1))) as send_mock,
                patch.object(pet, "save_state"),
                patch.object(pet, "console_log"),
            ):
                await pet.run_pet_scheduler(now)

            send_mock.assert_awaited_once_with(
                ".抚摸法宝 青竹蜂云剑（金雷竹·庚金相）",
                track=True,
                max_retry=1,
                reply_timeout=pet.PET_REPLY_TIMEOUT_SEC,
            )
            self.assertEqual(now + 1 + pet.PET_CD + 12, state_module.state["next_pet_time"])
            self.assertIn("等待回执", state_module.state["pet_last_error"])

            state_module.state["next_pet_time"] = now - 1
            state_module.state["pending_tasks"] = {
                7001: {
                    "cmd": ".抚摸法宝 青竹蜂云剑（金雷竹·庚金相）",
                    "sent_at": now,
                    "retry": 0,
                    "timeout": pet.PET_REPLY_TIMEOUT_SEC,
                }
            }
            with patch.object(pet, "send_game_command", new=AsyncMock()) as send_mock:
                await pet.run_pet_scheduler(now + 1)
            send_mock.assert_not_awaited()

    async def test_pet_scheduler_blocks_malformed_next_times_without_retry_spam(self):
        send_as_id = 8659059188
        now = 5000.0
        cases = [
            ("pet_enabled", "next_pet_time", "pet_last_error"),
            ("pet_trial_enabled", "next_pet_trial_time", "pet_trial_last_error"),
            ("pet_warm_enabled", "next_pet_warm_time", "pet_warm_last_error"),
            ("pet_formation_enabled", "next_pet_formation_time", "pet_formation_last_error"),
        ]
        state_module.ensure_identity_registered(send_as_id)

        for enabled_key, next_key, error_key in cases:
            with self.subTest(next_key=next_key), state_module.use_identity(send_as_id):
                state_module.state["pet_enabled"] = False
                state_module.state["pet_trial_enabled"] = False
                state_module.state["pet_warm_enabled"] = False
                state_module.state["pet_formation_enabled"] = False
                state_module.state["pending_tasks"] = {}
                state_module.state[enabled_key] = True
                state_module.state[next_key] = "冷却数据异常"
                state_module.state[error_key] = ""

                with (
                    patch.object(pet, "send_game_command", new=AsyncMock()) as send_mock,
                    patch.object(pet, "save_state") as save_mock,
                ):
                    await pet.run_pet_scheduler(now)

                send_mock.assert_not_awaited()
                save_mock.assert_not_called()
                self.assertEqual("冷却数据异常", state_module.state[next_key])
                self.assertEqual("", state_module.state[error_key])
                self.assertEqual({}, state_module.state["pending_tasks"])

    async def test_pet_touch_success_reply_confirms_and_clears_pending(self):
        send_as_id = 8659059189
        now = 6000.0
        command = ".抚摸法宝 青竹蜂云剑（金雷竹·庚金相）"
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, pet_name="青竹蜂云剑（金雷竹·庚金相）")
        reply_to = SimpleNamespace(id=7002, raw_text=command)
        text = "器灵 雷竹 亲昵地回应了你的安抚。（默契 +5，经验 +12）"

        with state_module.use_identity(send_as_id):
            state_module.state["pet_enabled"] = True
            state_module.state["pet_last_error"] = "法宝已发送，等待回执确认"
            state_module.state["pending_tasks"] = {
                7002: {
                    "cmd": command,
                    "sent_at": now - 20,
                    "retry": 0,
                    "timeout": pet.PET_REPLY_TIMEOUT_SEC,
                }
            }
            with patch.object(pet, "save_state"):
                handled = await pet.handle_pet_cd_fix(text, now, reply_to, matched_family="pet")

            self.assertTrue(handled)
            self.assertEqual({}, state_module.state["pending_tasks"])
            self.assertEqual(now + pet.PET_CD + pet.CD_BUFFER_SEC, state_module.state["next_pet_time"])
            self.assertEqual("", state_module.state["pet_last_error"])

    async def test_pet_trial_scheduler_uses_short_reply_timeout(self):
        send_as_id = 8659059190
        now = 7000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, pet_trial_name="青竹蜂云剑（庚金版）")

        with state_module.use_identity(send_as_id):
            state_module.state["pet_trial_enabled"] = True
            state_module.state["next_pet_trial_time"] = now - 1

            with (
                patch.object(pet.random, "uniform", return_value=42),
                patch.object(pet, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7003, sent_at=now + 1))) as send_mock,
                patch.object(pet, "save_state"),
                patch.object(pet, "console_log"),
            ):
                await pet.run_pet_scheduler(now)

            send_mock.assert_awaited_once_with(
                ".器灵试炼 青竹蜂云剑（庚金版）",
                track=True,
                max_retry=1,
                reply_timeout=pet.PET_REPLY_TIMEOUT_SEC,
            )
            self.assertEqual(now + 1 + pet.PET_TRIAL_CD + 42, state_module.state["next_pet_trial_time"])
            self.assertIn("等待回执", state_module.state["pet_trial_last_error"])

    async def test_pet_warm_scheduler_uses_short_reply_timeout(self):
        send_as_id = 8659059190
        now = 8000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, pet_warm_name="青竹蜂云剑（庚金版）")

        with state_module.use_identity(send_as_id):
            state_module.state["pet_warm_enabled"] = True
            state_module.state["next_pet_warm_time"] = now - 1

            with (
                patch.object(pet.random, "uniform", return_value=120),
                patch.object(pet, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7004, sent_at=now + 1))) as send_mock,
                patch.object(pet, "save_state"),
                patch.object(pet, "console_log"),
            ):
                await pet.run_pet_scheduler(now)

            send_mock.assert_awaited_once_with(
                ".温养器灵 青竹蜂云剑（庚金版）",
                track=True,
                max_retry=1,
                reply_timeout=pet.PET_REPLY_TIMEOUT_SEC,
            )
            self.assertEqual(now + 1 + pet.PET_WARM_CD + 120, state_module.state["next_pet_warm_time"])
            self.assertIn("等待回执", state_module.state["pet_warm_last_error"])

    async def test_pet_formation_scheduler_uses_module_managed_retry(self):
        send_as_id = 8659059194
        now = 8100.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["pet_formation_enabled"] = True
            state_module.state["next_pet_formation_time"] = now - 1

            with (
                patch.object(pet, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7005, sent_at=now + 1))) as send_mock,
                patch.object(pet, "save_state"),
                patch.object(pet, "console_log"),
            ):
                await pet.run_pet_scheduler(now)

            send_mock.assert_awaited_once_with(
                ".布下剑阵",
                track=True,
                max_retry=0,
                reply_timeout=pet.PET_REPLY_TIMEOUT_SEC,
                source_module="布下剑阵",
            )
            self.assertEqual(now + 1 + pet.PET_REPLY_TIMEOUT_SEC, state_module.state["next_pet_formation_time"])
            self.assertIn("等待回执", state_module.state["pet_formation_last_error"])
            self.assertEqual(0, state_module.state["pet_formation_retry_count"])

    async def test_pet_formation_timeout_retries_once_then_backs_off(self):
        send_as_id = 8659059194
        now = 8200.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["pet_formation_enabled"] = True
            state_module.state["next_pet_formation_time"] = now - 1
            state_module.state["pet_formation_last_error"] = "布下剑阵已发送，等待回执确认"
            state_module.state["pet_formation_retry_count"] = 0
            state_module.state["pending_tasks"] = {}

            with (
                patch.object(pet, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7006, sent_at=now + 1))) as send_mock,
                patch.object(pet, "save_state"),
                patch.object(pet, "console_log"),
            ):
                await pet.run_pet_scheduler(now)

            send_mock.assert_awaited_once()
            self.assertEqual(1, state_module.state["pet_formation_retry_count"])

            state_module.state["next_pet_formation_time"] = now + 60
            state_module.state["pet_formation_last_error"] = "布下剑阵已发送，等待回执确认"
            state_module.state["pet_formation_retry_count"] = 1
            with (
                patch.object(pet, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(pet, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(pet, "save_state"),
            ):
                await pet.run_pet_scheduler(now + 61)

            send_mock.assert_not_awaited()
            audit_mock.assert_awaited_once()
            self.assertEqual(0, state_module.state["pet_formation_retry_count"])
            self.assertIn("补发已达 1 次上限", state_module.state["pet_formation_last_error"])
            self.assertEqual(now + 61 + pet.PET_FORMATION_RETRY_BACKOFF_SEC, state_module.state["next_pet_formation_time"])

    async def test_pet_formation_timeout_recovers_logged_success_before_retry(self):
        send_as_id = 8659059194
        now = 8300.0
        state_module.ensure_identity_registered(send_as_id)
        sent_entry = {
            "message_id": 7005,
            "event_type": "sent",
            "text": ".布下剑阵",
            "source_module": "布下剑阵",
        }
        reply_entry = {
            "message_id": 7006,
            "reply_to_msg_id": 7005,
            "event_type": "message",
            "chat_id": state_module.get_game_group_id(),
            "sender_is_bot": True,
            "text": "剑阵已成！你消耗了 2000 点修为，布下了【大庚剑阵】！",
            "ts_epoch": now - 1,
        }

        with state_module.use_identity(send_as_id):
            state_module.state["pet_formation_enabled"] = True
            state_module.state["next_pet_formation_time"] = now - 1
            state_module.state["pet_formation_last_error"] = "布下剑阵已发送，等待回执确认"
            state_module.state["pet_formation_retry_count"] = 0
            state_module.state["pending_tasks"] = {}

            with patch.object(pet, "find_recent_message_log_command", return_value=sent_entry), patch.object(
                pet, "find_message_log_replies", return_value=[reply_entry]
            ), patch.object(pet, "send_game_command", new=AsyncMock()) as send_mock, patch.object(
                pet, "save_state"
            ), patch.object(pet, "console_log"):
                await pet.run_pet_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertEqual(now - 1 + pet.PET_FORMATION_BUFF_SEC, state_module.state["next_pet_formation_time"])
            self.assertEqual("", state_module.state["pet_formation_last_error"])
            self.assertEqual(0, state_module.state["pet_formation_retry_count"])

    async def test_pet_formation_success_sets_twelve_hour_timer(self):
        send_as_id = 8659059194
        now = 8300.0
        state_module.ensure_identity_registered(send_as_id)
        text = (
            "剑阵已成！\n"
            "你消耗了 2000 点修为，布下了【大庚剑阵】！\n"
            "在接下来的 720 分钟内，当你御使神雷版飞剑时，战力将大幅提升！"
        )
        reply_to = SimpleNamespace(raw_text=".布下剑阵")

        with state_module.use_identity(send_as_id):
            state_module.state["pet_formation_enabled"] = True
            state_module.state["pet_formation_last_error"] = "布下剑阵已发送，等待回执确认"
            state_module.state["pet_formation_retry_count"] = 1
            with patch.object(pet, "save_state"):
                handled = await pet.handle_pet_formation_reply(text, now, reply_to)

            self.assertTrue(handled)
            self.assertEqual(now + pet.PET_FORMATION_BUFF_SEC, state_module.state["next_pet_formation_time"])
            self.assertEqual("", state_module.state["pet_formation_last_error"])
            self.assertEqual(0, state_module.state["pet_formation_retry_count"])

    async def test_warm_success_sets_six_hour_timer(self):
        send_as_id = 8659059191
        now = 1000.0
        state_module.ensure_identity_registered(send_as_id)
        text = (
            "【温养器灵】\n"
            "你以灵石淬洗法宝灵窍，又焚化养魂木为引，细细温养 【青竹蜂云剑（庚金版）】。\n"
            "器灵 金竹郎 灵光大振，显得神完气足。\n\n"
            "- 消耗：灵石x3000、养魂木x3\n"
            "- 经验提升：+54"
        )

        with state_module.use_identity(send_as_id):
            state_module.state["pet_warm_enabled"] = True
            state_module.set_storage_bag_records({
                str(send_as_id): {
                    "updated_at": 900,
                    "items": {"灵石": 5000, "养魂木": 5},
                    "sections": {"材料": {"灵石": 5000, "养魂木": 5}},
                }
            })
            with (
                patch.object(pet.random, "uniform", return_value=120),
                patch.object(pet, "save_state"),
                patch.object(storage_bag, "save_state"),
            ):
                handled = await pet.handle_pet_warm_reply(text, now, None, matched_family="pet_warm")

            self.assertTrue(handled)
            self.assertEqual(now + pet.PET_WARM_CD + 120, state_module.state["next_pet_warm_time"])
            self.assertEqual("", state_module.state["pet_warm_last_error"])
            record = state_module.get_storage_bag_records()[str(send_as_id)]
            self.assertEqual(2000, record["items"]["灵石"])
            self.assertEqual(2, record["items"]["养魂木"])

    async def test_warm_cd_reply_uses_wait_time(self):
        send_as_id = 8659059192
        now = 2000.0
        state_module.ensure_identity_registered(send_as_id)
        text = "器灵方才吞纳过灵机，请在 5小时57分钟31秒 后再行温养。"
        reply_to = SimpleNamespace(raw_text=".温养器灵 青竹蜂云剑（庚金版）")

        with state_module.use_identity(send_as_id):
            state_module.state["pet_warm_enabled"] = True
            with patch.object(pet, "save_state"), patch.object(pet, "send_audit_log", new=AsyncMock()):
                handled = await pet.handle_pet_warm_reply(text, now, reply_to)

            self.assertTrue(handled)
            self.assertGreater(state_module.state["next_pet_warm_time"], now + 5 * 3600)
            self.assertEqual("", state_module.state["pet_warm_last_error"])

    async def test_warm_name_error_disables_module(self):
        send_as_id = 8659059193
        now = 3000.0
        state_module.ensure_identity_registered(send_as_id)
        text = "你没有这件拥有器灵的法宝，或者名字输入错误。"

        with state_module.use_identity(send_as_id):
            state_module.state["pet_warm_enabled"] = True
            with patch.object(pet, "save_state"), patch.object(pet, "send_audit_log", new=AsyncMock()):
                handled = await pet.handle_pet_warm_reply(text, now, None, matched_family="pet_warm")

            self.assertTrue(handled)
            self.assertFalse(state_module.state["pet_warm_enabled"])
            self.assertEqual(0, state_module.state["next_pet_warm_time"])
            self.assertIn("名称错误", state_module.state["pet_warm_last_error"])


if __name__ == "__main__":
    unittest.main()
