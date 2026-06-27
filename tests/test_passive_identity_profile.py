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

from model import app
from model import control
from model.config import CMD_IDENTITY_INFO, CMD_SECOND_SOUL_STATUS, CMD_YUANYING_STATUS
from model import state as state_module


COMBINED_CARD = """@jfdffdddd 的天命玉牒
────────────────
称号: 【紫灵的轻吻】
当前祭出: 【玄铁剑】, 【青竹蜂云剑】 (3/10)
已祭炼法宝: 2 件
宗门: 【凌霄宫】
灵根: 天灵根(火)
修为: 445955 / 1000000
丹毒: 0 点
杀戮: 0 人

【新手秘籍】
若有疑惑，可查阅《天道总纲》 (https://linux.do/t/topic/888560)。📊 【天机阁 · 战力评估】

👤 修士: 空尘子 (@jfdffdddd)
🏔️ 境界: 元婴中期 (凌霄宫)
⚔️ 综合战力: 333.8万
━━━━━━━━━━━━━━━
【力量构成】:
 - 基础修为: 135.2万
 - 祭出法宝:
   - 【玄铁剑】: +6.8万
   - 【青竹蜂云剑】: +74.4万
 - 灵根【天灵根(火)】: +32.4万
 - 【罡风淬体】(12层): +38.9万
 - 【周天巡天】(17轮): +26.0万"""


class PassiveIdentityProfileTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module.ensure_identity_registered(1001)
        state_module.set_send_as_profile(1001, username="jfdffdddd", label="jfdffdddd")

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    async def test_combined_identity_and_battle_card_updates_profile(self):
        with patch.object(control, "save_state"):
            handled = await control.handle_passive_identity_profile_card(COMBINED_CARD, 1_700_000_000)

        self.assertTrue(handled)
        profile = state_module.get_send_as_profile(1001)
        self.assertEqual("空尘子", profile["daohao"])
        self.assertEqual("元婴中期", profile["realm"])
        self.assertEqual("凌霄宫", profile["sect_name"])
        self.assertEqual("天灵根", profile["spiritual_root_type"])
        self.assertEqual("火", profile["spiritual_root_attrs"])
        self.assertEqual(445955, profile["xiuwei_current"])
        self.assertEqual(1000000, profile["xiuwei_max"])
        self.assertEqual("333.8万", profile["battle_power_text"])
        self.assertEqual(3338000, profile["battle_power_value"])

    async def test_refresh_identity_info_sends_level_read_commands(self):
        messages = [
            SimpleNamespace(id=11, sent_at=100.0),
            SimpleNamespace(id=12, sent_at=101.0),
            SimpleNamespace(id=13, sent_at=102.0),
        ]
        with (
            patch.object(control.time, "time", return_value=100.0),
            patch.object(control, "send_game_command", new=AsyncMock(side_effect=messages)) as send_mock,
            patch.object(control, "save_state"),
        ):
            ok, message = await control.refresh_identity_info(1001, source="ui", actor_id=123)

        self.assertTrue(ok)
        self.assertIn("元婴和第二元神", message)
        self.assertEqual(
            [CMD_IDENTITY_INFO, CMD_YUANYING_STATUS, CMD_SECOND_SOUL_STATUS],
            [call.args[0] for call in send_mock.await_args_list],
        )
        for call in send_mock.await_args_list:
            self.assertEqual(1001, call.kwargs["send_as_id"])
            self.assertEqual(1, call.kwargs["max_retry"])

    async def test_routed_identity_info_waiting_reply_keeps_refresh_pending_without_gap(self):
        with state_module.use_identity(1001) as identity_state:
            identity_state["pending_tasks"][501] = {
                "cmd": CMD_IDENTITY_INFO,
                "sent_at": 1000.0,
                "retry": 0,
                "timeout": 60,
                "reply_to_msg_id": 0,
                "priority": "normal",
                "max_retry": 1,
            }
            identity_state["last_identity_info_msg_id"] = 501
            identity_state["identity_info_reply_msg_ids"] = [501]
            identity_state["identity_info_last_requested_at"] = 1000.0

        reply_to = SimpleNamespace(id=501, raw_text=CMD_IDENTITY_INFO)
        reply_context = {
            "send_as_id": 1001,
            "family": "identity_info",
            "reply_to_msg_id": 501,
            "root_msg_id": 501,
        }

        with patch.object(app, "record_unhandled_routed_reply") as gap_mock, \
                patch.object(app, "schedule_cleanup", new=AsyncMock()):
            handled = await app._handle_routed_reply_event(
                SimpleNamespace(id=502, chat_id=-1001680975844, sender_id=8567800706),
                "正在为你绘制 @jfdffdddd 的身份玉牒...",
                1001.0,
                reply_to,
                reply_context,
                event_kind="edit",
            )

        self.assertFalse(handled)
        gap_mock.assert_not_called()
        with state_module.use_identity(1001) as identity_state:
            self.assertIn(501, identity_state["pending_tasks"])
            self.assertIn(502, identity_state["identity_info_reply_msg_ids"])
            self.assertEqual(502, identity_state["last_identity_info_msg_id"])

    async def test_routed_incomplete_identity_card_schedules_followup_without_gap(self):
        with state_module.use_identity(1001) as identity_state:
            identity_state["pending_tasks"][503] = {
                "cmd": CMD_IDENTITY_INFO,
                "sent_at": 1000.0,
                "retry": 0,
                "timeout": 60,
                "reply_to_msg_id": 0,
                "priority": "normal",
                "max_retry": 1,
            }
            identity_state["last_identity_info_msg_id"] = 503
            identity_state["identity_info_reply_msg_ids"] = [503]
            identity_state["identity_info_last_requested_at"] = 1000.0

        reply_to = SimpleNamespace(id=503, raw_text=CMD_IDENTITY_INFO)
        reply_context = {
            "send_as_id": 1001,
            "family": "identity_info",
            "reply_to_msg_id": 503,
            "root_msg_id": 503,
        }
        text = """@jfdffdddd 的天命玉牒
────────────────
宗门: 【太一门】
第二元神: Lv.31 (修炼中) - 战力: 1615047
灵根: 异灵根(雷)
修为: 107410 / 32000000
丹毒: 0 点
杀戮: 17 人"""

        with patch.object(app, "record_unhandled_routed_reply") as gap_mock, \
                patch.object(app, "schedule_cleanup", new=AsyncMock()), \
                patch.object(control.random, "randint", return_value=20), \
                patch.object(control, "save_state"):
            handled = await app._handle_routed_reply_event(
                SimpleNamespace(id=504, chat_id=-1001680975844, sender_id=8388633812),
                text,
                1001.0,
                reply_to,
                reply_context,
            )

        self.assertFalse(handled)
        gap_mock.assert_not_called()
        with state_module.use_identity(1001) as identity_state:
            self.assertNotIn(503, identity_state["pending_tasks"])
            self.assertIn(504, identity_state["identity_info_reply_msg_ids"])
            self.assertEqual(504, identity_state["last_identity_info_msg_id"])
            self.assertEqual(1021.0, identity_state["identity_info_followup_due_at"])

    async def test_unknown_owner_is_ignored(self):
        state_module.update_send_as_profile(1001, daohao="旧道号", realm="结丹后期")

        with patch.object(control, "save_state"):
            handled = await control.handle_passive_identity_profile_card(
                COMBINED_CARD.replace("@jfdffdddd", "@someone_else"),
                1_700_000_000,
            )

        self.assertFalse(handled)
        profile = state_module.get_send_as_profile(1001)
        self.assertEqual("旧道号", profile["daohao"])
        self.assertEqual("结丹后期", profile["realm"])

    async def test_battle_card_does_not_clear_existing_xiuwei(self):
        state_module.update_send_as_profile(
            1001,
            daohao="旧道号",
            realm="元婴初期",
            sect_name="旧宗门",
            xiuwei_current=123,
            xiuwei_max=500000,
        )
        battle_only = """📊 【天机阁 · 战力评估】

👤 修士: 空尘子 (@jfdffdddd)
🏔️ 境界: 元婴中期 (凌霄宫)
⚔️ 综合战力: 333.8万
 - 灵根【天灵根(火)】: +32.4万"""

        with patch.object(control, "save_state"):
            handled = await control.handle_passive_identity_profile_card(battle_only, 1_700_000_000)

        self.assertTrue(handled)
        profile = state_module.get_send_as_profile(1001)
        self.assertEqual("空尘子", profile["daohao"])
        self.assertEqual("元婴中期", profile["realm"])
        self.assertEqual("凌霄宫", profile["sect_name"])
        self.assertEqual(123, profile["xiuwei_current"])
        self.assertEqual(500000, profile["xiuwei_max"])
        self.assertEqual("333.8万", profile["battle_power_text"])

    async def test_wild_training_battle_text_is_ignored(self):
        text = "战力对比：你方 333.8万，妖兽 320万。"
        with patch.object(control, "save_state"):
            handled = await control.handle_passive_identity_profile_card(text, 1_700_000_000)
        self.assertFalse(handled)

    async def test_realm_breakthrough_does_not_downgrade_profile(self):
        state_module.update_send_as_profile(1001, daohao="空尘子", realm="结丹后期")
        text = "@jfdffdddd 灵光一闪，成功突破至【结丹中期】！"

        with patch.object(control, "save_state") as mock_save, \
             patch.object(control, "send_audit_log") as mock_audit:
            handled = await control.handle_realm_breakthrough_broadcast(text, 1_700_000_000)

        self.assertTrue(handled)
        self.assertEqual("结丹后期", state_module.get_send_as_profile(1001)["realm"])
        mock_save.assert_not_called()
        mock_audit.assert_awaited_once()

    async def test_realm_breakthrough_updates_forward_profile(self):
        state_module.update_send_as_profile(1001, daohao="空尘子", realm="结丹中期")
        text = "@jfdffdddd 灵光一闪，成功突破至【结丹后期】！"

        with patch.object(control, "save_state") as mock_save, \
             patch.object(control, "send_audit_log") as mock_audit:
            handled = await control.handle_realm_breakthrough_broadcast(text, 1_700_000_000)

        self.assertTrue(handled)
        self.assertEqual("结丹后期", state_module.get_send_as_profile(1001)["realm"])
        mock_save.assert_called_once()
        mock_audit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
