import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import config
from model import module_manifest


class ModuleManifestTests(unittest.TestCase):
    def test_manifest_covers_configured_modules(self):
        result = module_manifest.validate_module_manifest_coverage()

        self.assertTrue(result["ok"], result)
        self.assertEqual(set(config.MODULE_NAMES), set(config.MODULE_NAMES) & set(module_manifest.MODULE_MANIFESTS))

    def test_reply_family_maps_to_source_module(self):
        self.assertEqual("太一", module_manifest.get_module_name_for_reply_family("taiyi_yindao"))
        self.assertEqual("深度闭关", module_manifest.get_module_name_for_reply_family("deep_retreat"))
        self.assertEqual("储物袋", module_manifest.get_module_name_for_reply_family("storage_bag_buy"))

    def test_phaseful_modules_are_passive_first_last_resort_query(self):
        deep_retreat = module_manifest.get_module_manifest("深度闭关")
        yuanying = module_manifest.get_module_manifest("元婴")

        self.assertEqual(module_manifest.SEND_POLICY_PASSIVE_FIRST, deep_retreat.send_policy)
        self.assertEqual(module_manifest.ACTIVE_QUERY_LAST_RESORT, deep_retreat.active_query_policy)
        self.assertEqual(module_manifest.SEND_POLICY_PASSIVE_FIRST, yuanying.send_policy)
        self.assertEqual(module_manifest.ACTIVE_QUERY_LAST_RESORT, yuanying.active_query_policy)


if __name__ == "__main__":
    unittest.main()
