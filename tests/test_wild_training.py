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

from model import config
from model import runtime
from model import state as state_module
from model.features import passive_inbox, wild_training


class WildTrainingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _prepare_identity(self):
        send_as_id = 991201
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="wild")
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["wild_training_enabled"] = True
            identity_state["next_wild_training_time"] = 0
            identity_state["wild_training_reply_to_msg_id"] = 101
            identity_state["wild_training_reply_due_at"] = 1_700_000_600.0
            identity_state["wild_training_retry_count"] = 0
            identity_state["wild_training_last_msg_id"] = 101
            identity_state["wild_training_last_result"] = "已发送：谨慎"
            identity_state["wild_training_last_error"] = ""
        return send_as_id

    async def test_start_notice_keeps_pending_for_final_edit(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_000.0
        text = "【野外历练】\n@myios7 选择【谨慎】策略，正向荒野深处行去..."
        reply_to = SimpleNamespace(raw_text=f"{config.CMD_WILD_TRAINING} 谨慎", id=101)

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state"), \
             patch.object(wild_training, "console_log"):
            handled = await wild_training.handle_wild_training_reply(
                text,
                now,
                reply_to,
                matched_family="wild_training",
                current_msg_id=201,
            )

        self.assertTrue(handled)
        self.assertEqual(201, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(1_700_000_600.0, state_module.state["wild_training_reply_due_at"])
        self.assertEqual(0, state_module.state["wild_training_retry_count"])
        self.assertEqual(201, state_module.state["wild_training_last_msg_id"])
        self.assertEqual("已出发：谨慎", state_module.state["wild_training_last_result"])

    async def test_final_edit_clears_pending_and_records_rewards(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_010.0
        text = (
            "【野外历练 · 灵机暗藏】\n"
            "@myios7 在山涧残阵旁避开妖兽踪迹，采得一份机缘。\n"
            "获得修为 +392，获得 【清灵草】x1。"
        )
        reply_to = SimpleNamespace(raw_text=f"{config.CMD_WILD_TRAINING} 谨慎", id=101)

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state"), \
             patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_CYCLE_MIN_SEC), \
             patch.object(wild_training, "send_audit_log", new=AsyncMock()):
            handled = await wild_training.handle_wild_training_reply(
                text,
                now,
                reply_to,
                matched_family="wild_training",
                current_msg_id=201,
            )

        self.assertTrue(handled)
        self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(0, state_module.state["wild_training_reply_due_at"])
        self.assertEqual(201, state_module.state["wild_training_last_msg_id"])
        self.assertIn("修为+392", state_module.state["wild_training_last_result"])
        self.assertIn("清灵草x1", state_module.state["wild_training_last_result"])
        self.assertEqual(now + wild_training.WILD_TRAINING_CYCLE_MIN_SEC, state_module.state["next_wild_training_time"])

    async def test_started_timeout_schedules_next_without_retry(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["wild_training_reply_due_at"] = now - 1
            identity_state["wild_training_last_result"] = "已出发：谨慎"

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state"), \
             patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_CYCLE_MIN_SEC), \
             patch.object(wild_training, "send_audit_log", new=AsyncMock()):
            await wild_training.run_wild_training_scheduler(now)

        self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(0, state_module.state["wild_training_retry_count"])
        self.assertEqual(now + wild_training.WILD_TRAINING_CYCLE_MIN_SEC, state_module.state["next_wild_training_time"])
        self.assertIn("最终结果编辑", state_module.state["wild_training_last_error"])
        self.assertNotIn("准备补发", state_module.state["wild_training_last_error"])

    async def test_unanswered_command_still_retries_once(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_700.0
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["wild_training_reply_due_at"] = now - 1
            identity_state["wild_training_last_result"] = "已发送：谨慎"

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state"), \
             patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_RETRY_MIN_SEC), \
             patch.object(wild_training, "send_audit_log", new=AsyncMock()):
            await wild_training.run_wild_training_scheduler(now)

        self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(1, state_module.state["wild_training_retry_count"])
        self.assertEqual(now + wild_training.WILD_TRAINING_RETRY_MIN_SEC, state_module.state["next_wild_training_time"])
        self.assertIn("准备补发一次", state_module.state["wild_training_last_error"])

    async def test_passive_start_notice_does_not_clear_pending(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_000.0
        text = "【野外历练】\n@myios7 选择【谨慎】策略，正向荒野深处行去..."

        with state_module.use_identity(send_as_id):
            handled = passive_inbox._apply_wild_training_passive(text, now, "wild_training")

        self.assertTrue(handled)
        self.assertEqual(101, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(1_700_000_600.0, state_module.state["wild_training_reply_due_at"])
        self.assertEqual("已出发：谨慎", state_module.state["wild_training_last_result"])

    async def test_passive_result_without_identity_context_does_not_mutate_current_identity(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_010.0
        text = (
            "【野外历练 · 灵机暗藏】\n"
            "@myios7 在山涧残阵旁避开妖兽踪迹，采得一份机缘。\n"
            "获得修为 +392，获得 【清灵草】x1。"
        )

        with state_module.use_identity(send_as_id), \
             patch.object(passive_inbox, "_save_passive_stats"):
            handled = await passive_inbox.handle_passive_module_card(text, now=now, reply_context={})

        self.assertFalse(handled)
        self.assertEqual(101, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(1_700_000_600.0, state_module.state["wild_training_reply_due_at"])
        self.assertEqual("已发送：谨慎", state_module.state["wild_training_last_result"])

    async def test_pending_message_id_routes_after_restart(self):
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id):
            family = runtime._get_special_tracked_message_family(state_module.state, 101)

        self.assertEqual("wild_training", family)

    async def test_start_notice_message_id_routes_after_restart(self):
        send_as_id = self._prepare_identity()
        now = 1_700_000_000.0
        text = "【野外历练】\n@myios7 选择【谨慎】策略，正向荒野深处行去..."
        reply_to = SimpleNamespace(raw_text=f"{config.CMD_WILD_TRAINING} 谨慎", id=101)

        with state_module.use_identity(send_as_id), \
             patch.object(wild_training, "save_state"), \
             patch.object(wild_training, "console_log"):
            handled = await wild_training.handle_wild_training_reply(
                text,
                now,
                reply_to,
                matched_family="wild_training",
                current_msg_id=201,
            )
            family = runtime._get_special_tracked_message_family(state_module.state, 201)

        self.assertTrue(handled)
        self.assertEqual("wild_training", family)


if __name__ == "__main__":
    unittest.main()
