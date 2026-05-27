import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model import runtime
from model.features import dungeon_quiet


ACTIVE_NOTICE = """【坠魔谷·第一幕：裂隙外谷】
使用 .坠魔抉择 路径1/路径2/路径3 继续。

【稳控全场】已展开
副本结束前，天机阁将暂不响应本话题中的其他修仙指令。"""


class DungeonQuietTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_prepare_notice_does_not_activate_quiet_window(self):
        text = """你已祭出【稳控全场】，为本次【坠魔谷】立下静场令。
待队长正式带队进入副本后，直至副本结束前，天机阁将暂不响应本话题中的其他修仙指令。"""

        self.assertTrue(dungeon_quiet.is_dungeon_quiet_prepare_notice(text))
        self.assertIsNone(dungeon_quiet.observe_dungeon_quiet_text(text, now=1000))
        self.assertFalse(dungeon_quiet.is_dungeon_quiet_active(now=1000))

    def test_active_notice_sets_five_to_ten_minute_quiet_window(self):
        with patch.object(dungeon_quiet.random, "randint", return_value=420):
            result = dungeon_quiet.observe_dungeon_quiet_text(ACTIVE_NOTICE, now=1000)

        self.assertTrue(result["changed"])
        self.assertEqual(1420, result["until"])
        self.assertEqual("坠魔谷静场令", result["reason"])
        self.assertTrue(dungeon_quiet.is_dungeon_quiet_active(now=1419))
        self.assertFalse(dungeon_quiet.is_dungeon_quiet_active(now=1420))

    def test_active_notice_does_not_extend_existing_window(self):
        state_module.state["dungeon_quiet_until"] = 1500
        state_module.state["dungeon_quiet_reason"] = "已有静场令"

        with patch.object(dungeon_quiet.random, "randint", return_value=600):
            result = dungeon_quiet.observe_dungeon_quiet_text(ACTIVE_NOTICE, now=1000)

        self.assertFalse(result["changed"])
        self.assertEqual(1500, state_module.state["dungeon_quiet_until"])
        self.assertEqual("已有静场令", state_module.state["dungeon_quiet_reason"])

    def test_block_log_is_throttled(self):
        state_module.state["dungeon_quiet_last_log_at"] = 1000

        self.assertFalse(dungeon_quiet.should_log_dungeon_quiet_block(now=1059))
        self.assertTrue(dungeon_quiet.should_log_dungeon_quiet_block(now=1060))
        self.assertEqual(1060, state_module.state["dungeon_quiet_last_log_at"])


class DungeonQuietRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    async def test_quiet_window_blocks_normal_command(self):
        state_module.state["dungeon_quiet_until"] = 9999999999
        state_module.state["dungeon_quiet_reason"] = "坠魔谷静场令"
        state_module.state["dungeon_quiet_last_log_at"] = 0

        with patch.object(runtime, "send_audit_log", new_callable=unittest.mock.AsyncMock) as audit_mock:
            blocked = await runtime._dungeon_quiet_blocks_send(".小世界", runtime.SEND_PRIORITY_NORMAL, send_as_id=123)

        self.assertTrue(blocked)
        audit_mock.assert_awaited_once()

    async def test_quiet_window_allows_p0_command(self):
        state_module.state["dungeon_quiet_until"] = 9999999999
        state_module.state["dungeon_quiet_reason"] = "坠魔谷静场令"

        with patch.object(runtime, "send_audit_log", new_callable=unittest.mock.AsyncMock) as audit_mock:
            blocked = await runtime._dungeon_quiet_blocks_send(".自证 U9EX 15", runtime.SEND_PRIORITY_P0, send_as_id=123)

        self.assertFalse(blocked)
        audit_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
