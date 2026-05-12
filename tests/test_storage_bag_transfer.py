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
from model import ui


class StorageBagTransferTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self.source_id = 1001
        self.target_id = 1002
        state_module.ensure_identity_registered(self.source_id)
        state_module.ensure_identity_registered(self.target_id)
        state_module.set_send_as_profile(self.source_id, label="来源号", username="source")
        state_module.set_send_as_profile(self.target_id, label="目标号", username="target")
        state_module.set_storage_bag_records({
            str(self.source_id): {
                "updated_at": 1000,
                "items": {"妖丹": 9, "木髓": 4, "绑定物": 1},
            },
            str(self.target_id): {
                "updated_at": 1000,
                "items": {"灵石": 100, "标记物": 1},
            },
        })
        state_module.set_storage_bag_item_rules({
            "妖丹": {"method": "basic", "tags": ["材料"]},
            "木髓": {"method": "gift", "tags": ["材料"]},
            "绑定物": {"method": "blocked", "tags": ["特殊"]},
        })

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_snapshot_includes_rules_and_transfer_identities(self):
        snapshot = ui.get_storage_bag_snapshot()

        self.assertIn("item_rules", snapshot)
        self.assertEqual("买卖", snapshot["item_rules"]["妖丹"]["method_label"])
        self.assertEqual("赠送", snapshot["item_rules"]["木髓"]["method_label"])
        self.assertEqual("basic", snapshot["item_rules"]["灵石"]["method"])
        self.assertFalse(snapshot["item_rules"]["绑定物"]["transfer_selectable"])
        self.assertEqual({self.source_id, self.target_id}, {row["identity_id"] for row in snapshot["transfer_identities"]})

    def test_basic_transfer_preview_generates_listing_and_purchase_only(self):
        ok, message, preview = ui.ui_preview_storage_bag_transfer({
            "source_identity_id": self.source_id,
            "target_identity_id": self.target_id,
            "listing_item": "灵石",
            "items": [{"item_name": "妖丹", "quantity": 3}],
        })

        self.assertTrue(ok, message)
        self.assertEqual("已生成转移预览", message)
        self.assertEqual([
            {"identity_id": self.target_id, "command": ".上架 灵石 1 换 妖丹*3", "note": "目标身份上架换购物品"},
            {"identity_id": self.source_id, "command": ".购买 <挂单ID>", "note": "上架成功后来源身份购买挂单"},
        ], preview["commands"])
        self.assertIn("不会自动发送", preview["summary"])

    def test_gift_transfer_preview_uses_target_marker_and_source_gift_reply(self):
        ok, message, preview = ui.ui_preview_storage_bag_transfer({
            "source_identity_id": self.source_id,
            "target_identity_id": self.target_id,
            "items": [{"item_name": "木髓", "quantity": 2}],
        })

        self.assertTrue(ok, message)
        self.assertEqual([
            {"identity_id": self.target_id, "command": "转移标记 <本次转移ID>", "note": "目标身份先发送一条可回复的标记消息"},
            {"identity_id": self.source_id, "command": ".赠送 木髓 2", "note": "来源身份回复目标身份标记消息发送"},
        ], preview["commands"])

    def test_blocked_item_rejects_preview(self):
        ok, message, preview = ui.ui_preview_storage_bag_transfer({
            "source_identity_id": self.source_id,
            "target_identity_id": self.target_id,
            "items": [{"item_name": "绑定物", "quantity": 1}],
        })

        self.assertFalse(ok)
        self.assertEqual("绑定物 不可转移", message)
        self.assertIsNone(preview)

    def test_set_rule_persists_override_without_touching_command_send(self):
        with patch.object(ui, "send_game_command") as send_mock:
            ok, message = ui.ui_set_storage_bag_item_rule("妖丹", "gift", ["材料", "妖丹"], "测试")

        self.assertTrue(ok, message)
        self.assertEqual("gift", state_module.get_storage_bag_item_rules()["妖丹"]["method"])
        send_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
