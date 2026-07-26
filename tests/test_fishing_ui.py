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
        self.assertEqual("MiniApp", fishing["flow_mode"])
        self.assertEqual(20, fishing["daily_limit"])
        self.assertEqual(20, fishing["auto_buy_bait_count"])
        self.assertEqual(0, fishing["daily_count"])
        self.assertEqual({"day": "", "rods": 0, "fish": {}, "rewards": {}}, fishing["daily_catch_summary"])
        self.assertTrue(fishing["auto_chum_enabled"])
        self.assertEqual(["米糠小窝"], fishing["chum_names"])
        self.assertTrue(fishing["auto_buy_bait_enabled"])
        self.assertEqual(0, fishing["transfer_target_id"])
        self.assertEqual("关闭", fishing["transfer_target_label"])
        self.assertEqual([], fishing["transfer_identity_options"])
        self.assertEqual({}, fishing["caught_fish"])
        self.assertFalse(fishing["bait_inventory_known"])
        self.assertNotIn("auto_probe_enabled", fishing)
        self.assertNotIn("auto_open_fish_enabled", fishing)
        self.assertNotIn("cancel_after_sec", fishing)
        self.assertNotIn("plan", fishing)
        self.assertFalse(fishing["runtime"]["legacy_fallback"])
        self.assertFalse(fishing["runtime"]["available"])
        self.assertEqual("未配置公共入口", fishing["runtime"]["status"])

    def test_identity_snapshot_includes_daily_catch_summary_for_miniapp_ui(self):
        identity_state = state_module.get_identity_state(self.identity_id)
        identity_state["fishing_daily_catch_summary_json"] = (
            '{"day":"2026-07-06","rods":2,"fish":{"银须灵鲢":1},"rewards":{"幸运符":1}}'
        )

        fishing = ui.get_identity_ui_snapshot(self.identity_id)["fishing"]

        self.assertEqual("2026-07-06", fishing["daily_catch_summary"]["day"])
        self.assertEqual(2, fishing["daily_catch_summary"]["rods"])
        self.assertEqual({"银须灵鲢": 1}, fishing["daily_catch_summary"]["fish"])
        self.assertEqual({"幸运符": 1}, fishing["daily_catch_summary"]["rewards"])

    async def test_set_fishing_config_persists_miniapp_choices_without_legacy_fields(self):
        state_module.set_storage_bag_records({str(self.identity_id): {"items": {"灵米饵": 1, "灵石": 385, "凝血草": 5}, "sections": {}}})

        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()) as audit_mock:
            ok, message = await ui.ui_set_fishing_config(
                self.identity_id,
                {
                    "pond": "青溪浅滩",
                    "bait": "灵米饵",
                    "daily_limit": 7,
                    "auto_chum_enabled": True,
                    "chum_names": ["灵草窝"],
                    "auto_buy_bait_enabled": True,
                    "auto_buy_bait_count": 11,
                },
            )

        self.assertTrue(ok)
        self.assertNotIn(".买鱼饵", message)
        self.assertIn("未配置公共入口", message)
        identity_state = state_module.get_identity_state(self.identity_id)
        self.assertEqual("青溪浅滩", identity_state["fishing_pond"])
        self.assertEqual("灵米饵", identity_state["fishing_bait"])
        self.assertEqual(7, identity_state["fishing_daily_limit"])
        self.assertTrue(identity_state["fishing_auto_chum_enabled"])
        self.assertEqual("灵草窝", identity_state["fishing_chum_name"])
        self.assertEqual('["灵草窝"]', identity_state["fishing_chum_names"])
        self.assertTrue(identity_state["fishing_auto_buy_bait_enabled"])
        self.assertEqual(11, identity_state["fishing_auto_buy_bait_count"])
        audit_mock.assert_awaited_once()

        snapshot = ui.get_identity_ui_snapshot(self.identity_id)
        self.assertEqual(7, snapshot["fishing"]["daily_limit"])
        self.assertEqual(11, snapshot["fishing"]["auto_buy_bait_count"])
        self.assertNotIn("plan", snapshot["fishing"])
        self.assertFalse(snapshot["fishing"]["runtime"]["legacy_fallback"])

    async def test_set_fishing_config_clears_stale_forced_bait_when_bait_changes(self):
        identity_state = state_module.get_identity_state(self.identity_id)
        identity_state["fishing_forced_buy_bait"] = "凡饵"
        identity_state["fishing_forced_buy_count"] = 20
        state_module.set_storage_bag_records({str(self.identity_id): {"items": {"灵米饵": 0, "灵石": 1000, "凝血草": 10}, "sections": {}}})

        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message = await ui.ui_set_fishing_config(
                self.identity_id,
                {
                    "pond": "青溪浅滩",
                    "bait": "灵米饵",
                    "auto_buy_bait_enabled": True,
                    "auto_buy_bait_count": 20,
                },
            )

        self.assertTrue(ok)
        self.assertNotIn(".买鱼饵 凡饵", message)
        identity_state = state_module.get_identity_state(self.identity_id)
        self.assertEqual("", identity_state["fishing_forced_buy_bait"])
        self.assertEqual(0, identity_state["fishing_forced_buy_count"])
        self.assertNotIn("plan", ui.get_identity_ui_snapshot(self.identity_id)["fishing"])

    async def test_set_fishing_config_persists_transfer_target_and_rejects_invalid(self):
        target_id = 10002
        state_module.ensure_identity_registered(target_id)
        state_module.update_send_as_profile(target_id, username="target", label="目标号")

        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message = await ui.ui_set_fishing_config(
                self.identity_id,
                {
                    "pond": "青溪浅滩",
                    "bait": "凡饵",
                    "transfer_target_id": target_id,
                },
            )

        self.assertTrue(ok)
        self.assertIn("已更新灵溪垂钓", message)
        identity_state = state_module.get_identity_state(self.identity_id)
        self.assertEqual(target_id, identity_state["fishing_transfer_target_id"])
        snapshot = ui.get_identity_ui_snapshot(self.identity_id)
        self.assertEqual(target_id, snapshot["fishing"]["transfer_target_id"])
        self.assertEqual("目标号", snapshot["fishing"]["transfer_target_label"])
        self.assertEqual([target_id], [item["identity_id"] for item in snapshot["fishing"]["transfer_identity_options"]])

        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok_self, message_self = await ui.ui_set_fishing_config(
                self.identity_id,
                {"pond": "青溪浅滩", "bait": "凡饵", "transfer_target_id": self.identity_id},
            )
            ok_unknown, message_unknown = await ui.ui_set_fishing_config(
                self.identity_id,
                {"pond": "青溪浅滩", "bait": "凡饵", "transfer_target_id": 99999},
            )

        self.assertFalse(ok_self)
        self.assertIn("不能是当前身份", message_self)
        self.assertFalse(ok_unknown)
        self.assertIn("不存在", message_unknown)
        self.assertEqual(target_id, state_module.get_identity_state(self.identity_id)["fishing_transfer_target_id"])

    async def test_set_fishing_config_does_not_use_legacy_storage_plan_as_gate(self):
        state_module.set_storage_bag_records({str(self.identity_id): {"items": {"灵米饵": 1, "灵石": 35, "凝血草": 0}, "sections": {}}})

        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message = await ui.ui_set_fishing_config(
                self.identity_id,
                {
                    "pond": "青溪浅滩",
                    "bait": "灵米饵",
                    "auto_chum_enabled": True,
                    "chum_names": ["灵草窝"],
                    "auto_buy_bait_enabled": True,
                    "auto_buy_bait_count": 11,
                },
            )

        self.assertTrue(ok)
        self.assertNotIn("资源不足：", message)
        snapshot = ui.get_identity_ui_snapshot(self.identity_id)
        self.assertNotIn("plan", snapshot["fishing"])
        self.assertEqual(1, snapshot["fishing"]["bait_inventory"]["灵米饵"])

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
                    "chum_names": ["灵草窝"],
                    "auto_buy_bait_enabled": True,
                    "auto_buy_bait_count": 8,
                },
            )

        self.assertTrue(ok)
        self.assertNotIn(".打窝 灵草窝", message)
        snapshot = ui.get_identity_ui_snapshot(other_id)
        self.assertNotIn("plan", snapshot["fishing"])
        self.assertEqual("灵米饵", snapshot["fishing"]["bait"])

    async def test_set_fishing_config_clamps_daily_limit(self):
        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok_high, _message_high = await ui.ui_set_fishing_config(self.identity_id, {"pond": "青溪浅滩", "bait": "凡饵", "daily_limit": 99})
            high_limit = state_module.get_identity_state(self.identity_id)["fishing_daily_limit"]
            ok_low, _message_low = await ui.ui_set_fishing_config(self.identity_id, {"pond": "青溪浅滩", "bait": "凡饵", "daily_limit": 0})
            low_limit = state_module.get_identity_state(self.identity_id)["fishing_daily_limit"]

        self.assertTrue(ok_high)
        self.assertEqual(99, high_limit)
        self.assertTrue(ok_low)
        self.assertEqual(1, low_limit)

    async def test_set_fishing_config_rejects_invalid_choice(self):
        with patch.object(ui, "save_state"), patch.object(ui, "send_audit_log", new=AsyncMock()):
            ok, message = await ui.ui_set_fishing_config(self.identity_id, {"pond": "未知鱼塘", "bait": "凡饵"})

        self.assertFalse(ok)
        self.assertIn("无效的钓鱼配置", message)

    def test_index_loads_fishing_ui_after_app(self):
        html_text = (PROJECT_ROOT / "model/web/pages/index.html").read_text(encoding="utf-8")

        app_index = html_text.index("/static/js/app.js")
        fishing_index = html_text.index("/static/js/fishing_ui.js")
        self.assertLess(app_index, fishing_index)

    def test_fishing_ui_script_uses_fishing_config_endpoint(self):
        script = (PROJECT_ROOT / "model/web/static/js/fishing_ui.js").read_text(encoding="utf-8")

        self.assertIn("/api/fishing-config", script)
        self.assertIn("fishing-config-panel", script)
        self.assertIn("fishing-config-modal", script)
        self.assertIn("data-open-fishing-config", script)
        self.assertIn("data-open-fishing-config>设置</button>", script)
        self.assertNotIn("data-open-fishing-config>垂钓设置</button>", script)
        self.assertIn("renderFishingConfigModal(false)", script)
        self.assertIn("公共洞府 MiniApp｜页面内连钓｜无文本回退", script)
        self.assertNotIn("旧链兜底", script)
        self.assertNotIn("fishing-legacy-controls", script)
        self.assertIn("dailyCatchSummaryText", script)
        self.assertIn("compactCountMapText", script)
        self.assertIn('name="daily_limit"', script)
        self.assertIn('name="auto_buy_bait_count"', script)
        self.assertIn('name="chum_names"', script)
        self.assertIn('name="transfer_target_id"', script)
        self.assertNotIn('name="cancel_after_sec"', script)
        self.assertNotIn('name="auto_probe_enabled"', script)
        self.assertNotIn('name="auto_open_fish_enabled"', script)
        self.assertNotIn('select[name="chum_name"]', script)
        self.assertNotIn("resourceRequirementHtml", script)
        self.assertNotIn("clampCancelAfterSec", script)
        self.assertIn("baitInventoryText", script)
        self.assertIn("findFishingCard", script)
        self.assertIn("card.querySelector('.module-top')", script)
        self.assertIn("moduleTop.appendChild(panel)", script)
        self.assertNotIn("card.appendChild(panel)", script)
        self.assertNotIn('<details class="module-submenu fishing-submenu"', script)
        self.assertNotIn("grid.parentNode.insertBefore(panel, grid.nextSibling)", script)


if __name__ == "__main__":
    unittest.main()
