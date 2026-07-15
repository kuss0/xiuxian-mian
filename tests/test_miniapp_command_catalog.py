import unittest

from model import ui
from model.features import miniapp_command_catalog, miniapp_registry
from tools import miniapp_command_catalog_report


class MiniAppCommandCatalogTests(unittest.TestCase):
    def test_catalog_preserves_categories_and_intentional_dual_surface(self):
        snapshot = miniapp_command_catalog.build_command_catalog_snapshot()
        categories = {item["key"]: item for item in snapshot["categories"]}

        self.assertEqual(
            {"integrated", "sect_locked", "external_miniapp", "pending_migration", "chat_preserved"},
            set(categories),
        )
        self.assertIn(".洞府", [command for group in categories["integrated"]["groups"] for command in group["commands"]])
        self.assertIn(".天机试炼", [command for group in categories["external_miniapp"]["groups"] for command in group["commands"]])
        self.assertIn(".斗法", [command for group in categories["chat_preserved"]["groups"] for command in group["commands"]])
        self.assertEqual([".鬼赌坊"], snapshot["summary"]["multi_surface_commands"])
        self.assertIn(".琉璃塔榜", snapshot["summary"]["multi_group_commands"])
        self.assertEqual({"external_miniapp", "chat_preserved"}, set(snapshot["allowed_multi_surface"][".鬼赌坊"]))

    def test_validation_reports_world_boss_gap_without_reclassifying_it(self):
        validation = miniapp_command_catalog.validate_command_catalog(
            flow_plans=miniapp_registry.build_known_miniapp_flow_plans(),
            entry_probe_commands=ui.MINIAPP_ENTRY_PROBE_COMMANDS,
        )

        self.assertEqual("warn", validation["status"])
        self.assertEqual(0, validation["summary"]["errors"])
        issues = {(item["code"], item.get("command")) for item in validation["issues"]}
        self.assertIn(("flow_replacement_uncatalogued", ".世界boss"), issues)
        self.assertIn(("external_entry_not_automated", ".小药园"), issues)
        self.assertNotIn(("unapproved_multi_surface", ".鬼赌坊"), issues)

    def test_report_is_read_only_and_contains_checklist(self):
        report = miniapp_command_catalog_report.build_report()

        self.assertIn("no send", report["policy"])
        self.assertEqual("2026-07-15", report["catalog"]["version"])
        self.assertTrue(report["validation"]["checklist"])


if __name__ == "__main__":
    unittest.main()
