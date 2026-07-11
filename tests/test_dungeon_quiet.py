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

ACTIVE_NOTICE_REAL_20260531 = """【稳控全场】已展开
本次【虚天殿】进行期间，天机阁将暂不响应本话题中的其他修仙指令。"""

DUANWU_SETTLEMENT = """【端午镇蛟·龙舟破浪】
队伍沿芦苇浅湾缓缓推进，以粽叶灵符钉住水脉，五毒瘴虽盛，却始终未能掀翻龙舟。

通关保底：每位队员获得 1464灵石、800修为、20贡献。
幸运掉落：@guoke08 额外获得 【五色丝】x3。
最终判定：98分 | 毒潮风险：22"""

XIAOJI_SETTLEMENT = """【北冥小极宫·寒宫失利】
众人护着小极宫残阵撤退，冰海妖潮被阵门切断在外。

本次未能带出小极宫机缘，全员获得 1500修为、80贡献 作为补偿。
最终状态：寒焰 12 | 宫阵 88 | 鼎机 25 | 判定分 6527007"""


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
        self.assertEqual(0, state_module.state["dungeon_quiet_until"])
        self.assertEqual("", state_module.state["dungeon_quiet_reason"])

    def test_clear_expired_quiet_window_removes_stale_reason(self):
        state_module.state["dungeon_quiet_until"] = 1500
        state_module.state["dungeon_quiet_reason"] = "昆吾山静场令"
        state_module.state["dungeon_quiet_last_log_at"] = 1490

        self.assertTrue(dungeon_quiet.clear_expired_dungeon_quiet(now=1500))

        self.assertEqual(0, state_module.state["dungeon_quiet_until"])
        self.assertEqual("", state_module.state["dungeon_quiet_reason"])
        self.assertEqual(0, state_module.state["dungeon_quiet_last_log_at"])

    def test_settlement_notice_clears_active_quiet_window(self):
        state_module.state["dungeon_quiet_until"] = 2000
        state_module.state["dungeon_quiet_reason"] = "端午镇蛟静场令"
        state_module.state["dungeon_quiet_last_log_at"] = 1200

        result = dungeon_quiet.observe_dungeon_quiet_text(DUANWU_SETTLEMENT, now=1300)

        self.assertTrue(result["changed"])
        self.assertTrue(result["cleared"])
        self.assertEqual("端午镇蛟静场令", result["reason"])
        self.assertEqual(0, state_module.state["dungeon_quiet_until"])
        self.assertEqual("", state_module.state["dungeon_quiet_reason"])
        self.assertEqual(0, state_module.state["dungeon_quiet_last_log_at"])

    def test_settlement_notice_does_not_create_quiet_window(self):
        self.assertIsNone(dungeon_quiet.observe_dungeon_quiet_text(DUANWU_SETTLEMENT, now=1300))
        self.assertFalse(dungeon_quiet.is_dungeon_quiet_active(now=1300))

    def test_xiaoji_failure_settlement_clears_active_quiet_window(self):
        state_module.state["dungeon_quiet_until"] = 2000
        state_module.state["dungeon_quiet_reason"] = "北冥小极宫静场令"
        state_module.state["dungeon_quiet_last_log_at"] = 1200

        result = dungeon_quiet.observe_dungeon_quiet_text(XIAOJI_SETTLEMENT, now=1300)

        self.assertTrue(result["changed"])
        self.assertTrue(result["cleared"])
        self.assertEqual("北冥小极宫静场令", result["reason"])
        self.assertFalse(dungeon_quiet.is_dungeon_quiet_active(now=1300))

    def test_intermediate_dungeon_state_does_not_release_quiet_window(self):
        text = """【北冥小极宫·冰海妖围】
当前状态：寒焰 12 | 宫阵 72 | 鼎机 25 | 妖潮 30 | 判定分 100"""
        self.assertFalse(dungeon_quiet.is_dungeon_quiet_release_notice(text))

    def test_real_active_notice_with_dungeon_after_marker_sets_reason(self):
        self.assertTrue(dungeon_quiet.is_dungeon_quiet_active_notice(ACTIVE_NOTICE_REAL_20260531))

        with patch.object(dungeon_quiet.random, "randint", return_value=300):
            result = dungeon_quiet.observe_dungeon_quiet_text(ACTIVE_NOTICE_REAL_20260531, now=2000)

        self.assertTrue(result["changed"])
        self.assertEqual(2300, result["until"])
        self.assertEqual("虚天殿静场令", result["reason"])

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
        runtime._recent_dungeon_quiet_send_blocks.clear()

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        runtime._recent_dungeon_quiet_send_blocks.clear()
        super().tearDown()

    async def test_quiet_window_blocks_normal_command(self):
        state_module.state["dungeon_quiet_until"] = 9999999999
        state_module.state["dungeon_quiet_reason"] = "坠魔谷静场令"
        state_module.state["dungeon_quiet_last_log_at"] = 0

        with patch.object(runtime, "send_audit_log", new_callable=unittest.mock.AsyncMock) as audit_mock:
            blocked = await runtime._dungeon_quiet_blocks_send(".小世界", runtime.SEND_PRIORITY_NORMAL, send_as_id=123)

        self.assertTrue(blocked)
        audit_mock.assert_awaited_once()

    async def test_quiet_window_suppresses_followup_send_failure_audit(self):
        state_module.state["dungeon_quiet_until"] = 9999999999
        state_module.state["dungeon_quiet_reason"] = "昆吾山静场令"
        state_module.state["dungeon_quiet_last_log_at"] = 0

        send_mock = unittest.mock.AsyncMock(return_value=True)
        with patch.object(runtime, "_send_log_group_message", new=send_mock), \
                patch.object(runtime, "console_log") as console_mock:
            blocked = await runtime._dungeon_quiet_blocks_send(
                ".观星台",
                runtime.SEND_PRIORITY_NORMAL,
                send_as_id=123,
            )
            ok = await runtime.send_audit_log(
                "❌ 观星台发送失败，稍后重试。",
                scope="identity",
                send_as_id=123,
            )

        self.assertTrue(blocked)
        self.assertTrue(ok)
        send_mock.assert_awaited_once()
        self.assertIn("昆吾山静场令", send_mock.await_args.args[0])
        console_mock.assert_called_once()

    async def test_quiet_window_blocks_chain_command(self):
        state_module.state["dungeon_quiet_until"] = 9999999999
        state_module.state["dungeon_quiet_reason"] = "虚天殿静场令"
        state_module.state["dungeon_quiet_last_log_at"] = 0

        with patch.object(runtime, "send_audit_log", new_callable=unittest.mock.AsyncMock) as audit_mock:
            blocked = await runtime._dungeon_quiet_blocks_send("1", runtime.SEND_PRIORITY_CHAIN, send_as_id=123)

        self.assertTrue(blocked)
        audit_mock.assert_awaited_once()

    async def test_quiet_window_allows_p0_command(self):
        state_module.state["dungeon_quiet_until"] = 9999999999
        state_module.state["dungeon_quiet_reason"] = "坠魔谷静场令"

        with patch.object(runtime, "send_audit_log", new_callable=unittest.mock.AsyncMock) as audit_mock:
            blocked = await runtime._dungeon_quiet_blocks_send(".自证 U9EX 15", runtime.SEND_PRIORITY_P0, send_as_id=123)

        self.assertFalse(blocked)
        audit_mock.assert_not_awaited()

    async def test_quiet_window_only_allows_known_replica_choice_commands(self):
        state_module.state["dungeon_quiet_until"] = 9999999999
        state_module.state["dungeon_quiet_reason"] = "昆吾山静场令"
        state_module.state["dungeon_quiet_last_log_at"] = 0

        with patch.object(runtime, "send_audit_log", new_callable=unittest.mock.AsyncMock) as audit_mock:
            self.assertFalse(await runtime._dungeon_quiet_blocks_send(".选择 岔路1", runtime.SEND_PRIORITY_NORMAL, send_as_id=123))
            self.assertFalse(await runtime._dungeon_quiet_blocks_send(".选择 强行摘取", runtime.SEND_PRIORITY_NORMAL, send_as_id=123))
            self.assertFalse(await runtime._dungeon_quiet_blocks_send(".选择 静待时机", runtime.SEND_PRIORITY_NORMAL, send_as_id=123))
            self.assertFalse(await runtime._dungeon_quiet_blocks_send(".坠魔抉择 路径1", runtime.SEND_PRIORITY_NORMAL, send_as_id=123))
            self.assertFalse(await runtime._dungeon_quiet_blocks_send(".黄龙抉择 1", runtime.SEND_PRIORITY_NORMAL, send_as_id=123))
            self.assertFalse(await runtime._dungeon_quiet_blocks_send(".苍坤抉择 1", runtime.SEND_PRIORITY_NORMAL, send_as_id=123))
            self.assertFalse(await runtime._dungeon_quiet_blocks_send(".落云抉择 1", runtime.SEND_PRIORITY_NORMAL, send_as_id=123))
            self.assertFalse(await runtime._dungeon_quiet_blocks_send(".进入落云秘圃", runtime.SEND_PRIORITY_NORMAL, send_as_id=123))
            self.assertTrue(await runtime._dungeon_quiet_blocks_send(".选择 随便", runtime.SEND_PRIORITY_NORMAL, send_as_id=123))

        audit_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
