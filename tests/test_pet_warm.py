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
                "ADMIN_ID=0",
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
from model.features import pet


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class PetWarmTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
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
            with patch.object(pet.random, "uniform", return_value=120), patch.object(pet, "save_state"):
                handled = await pet.handle_pet_warm_reply(text, now, None, matched_family="pet_warm")

            self.assertTrue(handled)
            self.assertEqual(now + pet.PET_WARM_CD + 120, state_module.state["next_pet_warm_time"])
            self.assertEqual("", state_module.state["pet_warm_last_error"])

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
