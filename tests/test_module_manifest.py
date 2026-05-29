import ast
import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import config
from model import module_manifest


def _literal_first_args_for_call(source_path, attr_name):
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != attr_name:
            continue
        if not node.args:
            continue
        first_arg = node.args[0]
        if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
            names.add(first_arg.value)
    return names


class ModuleManifestTests(unittest.TestCase):
    def test_manifest_covers_configured_modules(self):
        result = module_manifest.validate_module_manifest_coverage()

        self.assertTrue(result["ok"], result)
        self.assertEqual(set(config.MODULE_NAMES), set(config.MODULE_NAMES) & set(module_manifest.MODULE_MANIFESTS))

    def test_reply_family_maps_to_source_module(self):
        self.assertEqual("太一", module_manifest.get_module_name_for_reply_family("taiyi_yindao"))
        self.assertEqual("深度闭关", module_manifest.get_module_name_for_reply_family("deep_retreat"))
        self.assertEqual("储物袋", module_manifest.get_module_name_for_reply_family("storage_bag_buy"))
        self.assertEqual("自动副本", module_manifest.get_module_name_for_reply_family("dungeon_join"))
        self.assertEqual("共历心劫", module_manifest.get_module_name_for_reply_family("concubine_heart"))

    def test_workflow_names_map_to_manifest_owner(self):
        self.assertEqual("太一", module_manifest.get_module_name_for_workflow("taiyi"))
        self.assertEqual("深度闭关", module_manifest.get_module_name_for_workflow("deep_retreat"))
        self.assertEqual("储物袋", module_manifest.get_module_name_for_workflow("storage_bag_transfer"))
        self.assertEqual("自动副本", module_manifest.get_module_name_for_workflow("dungeon_join"))
        self.assertEqual("侍妾", module_manifest.get_module_name_for_workflow("concubine"))

    def test_runtime_reply_families_are_manifested(self):
        from model import runtime

        missing = sorted(
            family
            for family in runtime.REPLY_FAMILY_COMMANDS
            if not module_manifest.get_module_name_for_reply_family(family)
        )

        self.assertEqual([], missing)

    def test_workflow_log_names_are_manifested(self):
        workflow_names = set()
        for source_path in sorted((PROJECT_ROOT / "model" / "features").glob("*.py")):
            workflow_names.update(_literal_first_args_for_call(source_path, "append_workflow_event"))

        missing = sorted(
            workflow
            for workflow in workflow_names
            if not module_manifest.get_module_name_for_workflow(workflow)
        )

        self.assertEqual([], missing)

    def test_real_message_sample_families_are_manifested(self):
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"
        samples = json.loads(fixture_path.read_text(encoding="utf-8"))
        missing = sorted(
            sample_id
            for sample_id, payload in samples.items()
            if payload.get("family") and not module_manifest.get_module_name_for_reply_family(payload.get("family"))
        )

        self.assertEqual([], missing)

    def test_real_message_samples_match_manifest_replay_modules(self):
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"
        samples = json.loads(fixture_path.read_text(encoding="utf-8"))
        result = module_manifest.validate_replay_sample_coverage(samples)

        self.assertTrue(result["ok"], result)
        self.assertEqual([], result["missing_sample_sources"])
        self.assertEqual([], result["missing_sample_modules"])
        self.assertEqual([], result["missing_sample_families"])
        self.assertEqual("自动副本", module_manifest.get_module_name_for_replay_module("join_dungeon"))
        self.assertEqual("元婴", module_manifest.get_module_name_for_replay_module("yuanying"))

    def test_phaseful_modules_are_passive_first_last_resort_query(self):
        deep_retreat = module_manifest.get_module_manifest("深度闭关")
        yuanying = module_manifest.get_module_manifest("元婴")

        self.assertEqual(module_manifest.SEND_POLICY_PASSIVE_FIRST, deep_retreat.send_policy)
        self.assertEqual(module_manifest.ACTIVE_QUERY_LAST_RESORT, deep_retreat.active_query_policy)
        self.assertEqual(module_manifest.SEND_POLICY_PASSIVE_FIRST, yuanying.send_policy)
        self.assertEqual(module_manifest.ACTIVE_QUERY_LAST_RESORT, yuanying.active_query_policy)


if __name__ == "__main__":
    unittest.main()
