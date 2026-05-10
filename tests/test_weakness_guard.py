import atexit
import copy
import os
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

from model import runtime
from model import state as state_module
from model.features import taiyi
from model.config import TAIYI_CYCLE_CD_SEC


def _close_coro(coro):
    coro.close()


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class WeaknessGuardTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    async def test_note_identity_weakness_records_until_and_blocks_touch(self):
        identity_id = 8659059191
        now = 1000.0
        state_module.ensure_identity_registered(identity_id)
        text = "🚫 虚弱状态\n暂时无法运转灵力，请在洞府中静养 29分钟。"

        with patch.object(runtime, "_fire_and_forget", side_effect=_close_coro):
            self.assertTrue(runtime.note_identity_weakness(text, now, identity_id))

        identity_state = state_module.get_identity_state(identity_id)
        self.assertGreater(identity_state["weak_until"], now + 29 * 60)
        self.assertTrue(runtime.is_identity_weak(identity_id, now + 60))
        self.assertFalse(runtime._weakness_allows_command(".抚摸法宝 玄天斩灵剑"))
        self.assertTrue(runtime._weakness_allows_command(".修理法宝 玄天斩灵剑"))

    async def test_taiyi_disaster_closes_cycle_and_records_weakness(self):
        identity_id = 8659059191
        now = 2000.0
        state_module.ensure_identity_registered(identity_id)
        disaster_text = (
            "【大凶之兆】\n"
            "空间乱流发生暴动！一场恐怖的虚空风暴毫无征兆地席卷而来！\n"
            "修为损失了 122498 点，并陷入了30分钟的【虚弱状态】！"
        )
        reply_to = SimpleNamespace(id=1234, raw_text=".搜寻节点")

        with state_module.use_identity(identity_id):
            state_module.state["taiyi_enabled"] = True
            state_module.state["taiyi_node_search_enabled"] = True
            state_module.state["taiyi_phase"] = "search_pending"
            state_module.state["taiyi_phase_entered_at"] = now - 10
            state_module.state["taiyi_node_search_msg_id"] = 1234
            state_module.state["next_taiyi_cycle_time"] = now + 3600
            with patch.object(runtime, "_fire_and_forget", side_effect=_close_coro), patch.object(taiyi, "send_audit_log", new=AsyncMock()):
                handled = await taiyi.handle_taiyi_node_search_reply(
                    disaster_text,
                    now,
                    reply_to,
                    matched_family="taiyi_node_search",
                )

            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["taiyi_phase"])
            self.assertEqual(0, state_module.state["taiyi_node_search_msg_id"])
            self.assertGreaterEqual(state_module.state["next_taiyi_cycle_time"], now + TAIYI_CYCLE_CD_SEC)
            self.assertTrue(runtime.is_identity_weak(identity_id, now + 60))


if __name__ == "__main__":
    unittest.main()
