import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model import ui


class FishingUiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module.set_storage_bag_records({})
        self.identity_id = 10001
        state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(self.identity_id, username="source", label="来源号")

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_identity_snapshot_includes_fishing_config_and_unknown_inventory_does_not_block(self):
        snapshot = ui.get_identity_ui_snapshot(self.identity_id)

        fishing = snapshot["fishing"]
        self.assertEqual("青溪浅滩", fishing["pond"])
        self.assertEqual("凡饵", fishing["bait"])
        self.assertEqual(20, fishing["daily_limit"])
        self.assertEqual(8, fishing["auto_buy_bait_count"])
        self.assertEqual(0, fishing["daily_count"])
        self.assertFalse(fishing["auto_chum_enabled"])
        self.assertFalse(fishing["bait_inventory_known"])
        self.assertTrue(fishing["plan"]["allow_start"])
        self.assertEqual([".钓鱼 青溪浅滩 凡饵"], fishing["plan"]["commands"])

    async def test_set_fishing_config_persists_choices_and_plans_missing_bait_purchase(self):
        state_module.set_storage_bag_records({str(self.identity_id): {"items": {"灵米饵": 1, "灵石": 385, "凝血草": 5}, "sections": {}}})

        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()) as audit_mock:
            ok, message = await ui.ui_set_fishing_config(
                self.identity_id,
                {
                    "pond": "青溪浅滩",
                    "bait": "灵米饵",
                    "daily_limit": 7,
                    "auto_chum_enabled": True,
                    "chum_name": "灵草窝",
                    "auto_buy_bait_enabled": True,
                    "auto_buy_bait_count": 11,
                    "auto_probe_enabled": True,
                },
            )

        self.assertTrue(ok)
        self.assertIn(".买鱼饵 灵米饵 11", message)
        identity_state = state_module.get_identity_state(self.identity_id)
        self.assertEqual("青溪浅滩", identity_state["fishing_pond"])
        self.assertEqual("灵米饵", identity_state["fishing_bait"])
        self.assertEqual(7, identity_state["fishing_daily_limit"])
        self.assertTrue(identity_state["fishing_auto_chum_enabled"])
        self.assertEqual("灵草窝", identity_state["fishing_chum_name"])
        self.assertTrue(identity_state["fishing_auto_buy_bait_enabled"])
        self.assertEqual(11, identity_state["fishing_auto_buy_bait_count"])
        self.assertTrue(identity_state["fishing_auto_probe_enabled"])
        audit_mock.assert_awaited_once()

        snapshot = ui.get_identity_ui_snapshot(self.identity_id)
        self.assertEqual([".买鱼饵 灵米饵 11"], snapshot["fishing"]["plan"]["purchase_commands"])
        self.assertEqual(7, snapshot["fishing"]["daily_limit"])
        self.assertEqual(11, snapshot["fishing"]["auto_buy_bait_count"])
        self.assertTrue(snapshot["fishing"]["plan"]["allow_start"])
        self.assertEqual([], [item for item in snapshot["fishing"]["plan"]["resource_requirements"] if item["missing_count"]])

    async def test_set_fishing_config_shows_resource_shortage_in_plan(self):
        state_module.set_storage_bag_records({str(self.identity_id): {"items": {"灵米饵": 1, "灵石": 35, "凝血草": 0}, "sections": {}}})

        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message = await ui.ui_set_fishing_config(
                self.identity_id,
                {
                    "pond": "青溪浅滩",
                    "bait": "灵米饵",
                    "auto_chum_enabled": True,
                    "chum_name": "灵草窝",
                    "auto_buy_bait_enabled": True,
                    "auto_buy_bait_count": 11,
                },
            )

        self.assertTrue(ok)
        self.assertIn("资源不足：", message)
        snapshot = ui.get_identity_ui_snapshot(self.identity_id)
        self.assertFalse(snapshot["fishing"]["plan"]["allow_start"])
        self.assertIn("凝血草x5", snapshot["fishing"]["plan"]["blocked_reason"])
        self.assertEqual([".买鱼饵 灵米饵 11"], snapshot["fishing"]["plan"]["purchase_commands"])
        missing_resources = {item["item_name"]: item["missing_count"] for item in snapshot["fishing"]["plan"]["resource_requirements"]}
        self.assertEqual(5, missing_resources["凝血草"])

    async def test_set_fishing_config_uses_target_identity_for_plan(self):
        other_id = 10002
        state_module.ensure_identity_registered(other_id)
        state_module.update_send_as_profile(other_id, username="target", label="目标号")
        state_module.set_storage_bag_records({
            str(other_id): {"items": {"灵米饵": 3, "灵石": 1000, "凝血草": 10}, "sections": {}},
        })
        with state_module.use_identity(other_id):
            state_module.state["fishing_active_chum_name"] = "灵草窝"
            state_module.state["fishing_chum_rods_remaining"] = 3

        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message = await ui.ui_set_fishing_config(
                other_id,
                {
                    "pond": "青溪浅滩",
                    "bait": "灵米饵",
                    "auto_chum_enabled": True,
                    "chum_name": "灵草窝",
                    "auto_buy_bait_enabled": True,
                    "auto_buy_bait_count": 8,
                },
            )

        self.assertTrue(ok)
        self.assertNotIn(".打窝 灵草窝", message)
        snapshot = ui.get_identity_ui_snapshot(other_id)
        self.assertEqual([".钓鱼 青溪浅滩 灵米饵"], snapshot["fishing"]["plan"]["commands"])

    async def test_set_fishing_config_clamps_daily_limit(self):
        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok_high, _message_high = await ui.ui_set_fishing_config(self.identity_id, {"pond": "青溪浅滩", "bait": "凡饵", "daily_limit": 99})
            high_limit = state_module.get_identity_state(self.identity_id)["fishing_daily_limit"]
            ok_low, _message_low = await ui.ui_set_fishing_config(self.identity_id, {"pond": "青溪浅滩", "bait": "凡饵", "daily_limit": 0})
            low_limit = state_module.get_identity_state(self.identity_id)["fishing_daily_limit"]

        self.assertTrue(ok_high)
        self.assertEqual(20, high_limit)
        self.assertTrue(ok_low)
        self.assertEqual(1, low_limit)

    async def test_set_fishing_config_rejects_invalid_choice(self):
        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message = await ui.ui_set_fishing_config(self.identity_id, {"pond": "未知鱼塘", "bait": "凡饵"})

        self.assertFalse(ok)
        self.assertIn("无效的钓鱼配置", message)

    def test_index_loads_fishing_ui_after_app(self):
        html_text = (PROJECT_ROOT / "model/web/pages/index.html").read_text(encoding="utf-8")

        app_index = html_text.index("<script src='/static/js/app.js'></script>")
        fishing_index = html_text.index("<script src='/static/js/fishing_ui.js'></script>")
        self.assertLess(app_index, fishing_index)

    def test_fishing_ui_script_uses_fishing_config_endpoint(self):
        script = (PROJECT_ROOT / "model/web/static/js/fishing_ui.js").read_text(encoding="utf-8")

        self.assertIn("/api/fishing-config", script)
        self.assertIn("fishing-config-panel", script)
        self.assertIn('name="daily_limit"', script)
        self.assertIn('name="auto_buy_bait_count"', script)
        self.assertIn("resourceRequirementHtml", script)
        self.assertIn("findFishingCard", script)
        self.assertIn("card.appendChild(panel)", script)
        self.assertNotIn("grid.parentNode.insertBefore(panel, grid.nextSibling)", script)


if __name__ == "__main__":
    unittest.main()
