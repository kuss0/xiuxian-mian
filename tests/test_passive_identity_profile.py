import atexit
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


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

from model import control
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
