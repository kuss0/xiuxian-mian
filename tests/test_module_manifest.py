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

    def test_behavior_specs_cover_manifest_entries(self):
        result = module_manifest.validate_behavior_spec_coverage()

        self.assertTrue(result["ok"], result)
        self.assertEqual(
            [manifest.name for manifest in module_manifest.iter_module_manifests()],
            [spec.name for spec in module_manifest.iter_behavior_specs()],
        )

    def test_behavior_execution_order_is_stable(self):
        self.assertEqual(
            [
                "元婴",
                "深度闭关",
                "周天星斗",
                "合欢宗",
                "天星宗",
                "阴罗宗",
                "真仙试锋",
                "探寻裂缝",
                "观星监控",
                "法宝",
                "温养器灵",
                "器灵试炼",
                "放养",
                "野外历练",
                "观星台",
                "观星",
                "登天阶",
                "玄骨考校",
                "极阴祖师",
                "侍妾",
                "天机代卜",
                "共历心劫",
                "侍妾远航",
                "南陇侯",
                "问道",
                "小世界",
                "卜筮问天",
                "点卯",
                "宗门传功",
                "闯塔",
                "第二元神",
                "太一",
                "自动副本",
                "储物袋",
            ],
            [spec.name for spec in module_manifest.execution_order()],
        )

    def test_reply_family_maps_to_source_module(self):
        self.assertEqual("灵树", module_manifest.get_module_name_for_reply_family("tree_panel"))
        self.assertTrue(module_manifest.is_reply_family_archived("tree_panel"))
        self.assertEqual("太一", module_manifest.get_module_name_for_reply_family("taiyi_yindao"))
        self.assertEqual("深度闭关", module_manifest.get_module_name_for_reply_family("deep_retreat"))
        self.assertEqual("储物袋", module_manifest.get_module_name_for_reply_family("storage_bag_buy"))
        self.assertEqual("自动副本", module_manifest.get_module_name_for_reply_family("dungeon_join"))
        self.assertEqual("共历心劫", module_manifest.get_module_name_for_reply_family("concubine_heart"))
        self.assertEqual("合欢宗", module_manifest.get_module_name_for_reply_family("hehuan_dual"))
        self.assertEqual("天星宗", module_manifest.get_module_name_for_reply_family("tianxing_panel"))
        self.assertEqual("阴罗宗", module_manifest.get_module_name_for_reply_family("yinluo_banner"))
        self.assertEqual("阴罗宗", module_manifest.get_module_name_for_reply_family("yinluo_daily_sacrifice"))
        self.assertEqual("点卯", module_manifest.get_module_name_for_reply_family("checkin"))
        self.assertEqual("宗门传功", module_manifest.get_module_name_for_reply_family("sect_teach"))
        self.assertEqual("探寻裂缝", module_manifest.get_module_name_for_reply_family("explore_rift"))
        self.assertEqual("问道", module_manifest.get_module_name_for_reply_family("wendao"))
        self.assertEqual("周天星斗", module_manifest.get_module_name_for_reply_family("formation_start"))
        self.assertEqual("周天星斗", module_manifest.get_module_name_for_reply_family("formation_assist"))

    def test_reply_families_have_single_behavior_owner(self):
        owners = {}
        duplicates = []
        for spec in module_manifest.iter_behavior_specs():
            for family in spec.reply_families:
                if family in owners:
                    duplicates.append((family, owners[family], spec.name))
                owners[family] = spec.name

        self.assertEqual([], duplicates)
        self.assertEqual("太一", owners["taiyi_yindao"])
        self.assertEqual("自动副本", owners["replica_join"])
        self.assertEqual("储物袋", owners["storage_bag_buy"])

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

    def test_active_manifest_reply_families_are_runtime_command_routable_or_passive_only(self):
        from model import runtime

        passive_only_families = {
            # These are real local-text observations that come from another
            # command or a passive game broadcast, not from sending the family.
            "tianxing_modifier",
            "tianxing_retreat",
            "yinluo_retreat",
            "dungeon_join",
        }
        missing = sorted(
            family
            for manifest in module_manifest.iter_module_manifests(include_archived=False)
            for family in tuple(manifest.reply_families or ())
            if family not in runtime.REPLY_FAMILY_COMMANDS and family not in passive_only_families
        )

        self.assertEqual([], missing)
        for family in passive_only_families:
            self.assertTrue(module_manifest.get_module_name_for_reply_family(family), family)
            self.assertNotIn(family, runtime.REPLY_FAMILY_COMMANDS)

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
        self.assertEqual("合欢宗", module_manifest.get_module_name_for_replay_module("hehuan"))
        self.assertEqual("天星宗", module_manifest.get_module_name_for_replay_module("tianxing"))
        self.assertEqual("阴罗宗", module_manifest.get_module_name_for_replay_module("yinluo"))
        self.assertEqual("观星台", module_manifest.get_module_name_for_replay_module("stargazer"))
        self.assertEqual("探寻裂缝", module_manifest.get_module_name_for_replay_module("explore_rift"))
        self.assertEqual("问道", module_manifest.get_module_name_for_replay_module("wendao"))
        self.assertEqual("周天星斗", module_manifest.get_module_name_for_replay_module("formation"))
        self.assertEqual("法宝", module_manifest.get_module_name_for_replay_module("pet"))
        self.assertEqual("闯塔", module_manifest.get_module_name_for_replay_module("tower"))
        self.assertEqual("灵树", module_manifest.get_module_name_for_replay_module("灵树"))

    def test_real_message_sample_module_can_use_manifest_name_without_replay_alias(self):
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"
        samples = json.loads(fixture_path.read_text(encoding="utf-8"))
        samples["tree.panel"] = {
            "source": "unit",
            "module": "灵树",
            "family": "tree_panel",
            "event_type": "message",
            "text": "【灵树状态】 灵气充盈。",
        }

        result = module_manifest.validate_replay_sample_coverage(samples)

        self.assertTrue(result["ok"], result)
        self.assertEqual([], result["unknown_sample_modules"])
        self.assertEqual([], result["unknown_sample_families"])

    def test_module_admission_contract_accepts_current_manifest_and_strict_samples(self):
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"
        samples = json.loads(fixture_path.read_text(encoding="utf-8"))
        result = module_manifest.validate_module_admission_contract(
            samples,
            strict_modules=("太一", "自动副本", "储物袋", "阴罗宗", "观星台"),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual([], result["missing_duplicate_guard"])
        self.assertEqual([], result["last_resort_without_passive_first"])
        self.assertEqual([], result["passive_without_observation"])
        self.assertEqual([], result["strict_missing_replay_routes"])
        self.assertEqual([], result["strict_missing_samples"])

    def test_stargazer_replay_alias_satisfies_strict_admission(self):
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"
        samples = json.loads(fixture_path.read_text(encoding="utf-8"))
        for payload in samples.values():
            if str(payload.get("family") or "").startswith("stargazer_"):
                payload["module"] = "stargazer"

        replay_result = module_manifest.validate_replay_sample_coverage(samples)
        admission_result = module_manifest.validate_module_admission_contract(
            samples,
            strict_modules=("观星台",),
        )

        self.assertEqual("观星台", module_manifest.get_module_name_for_replay_module("stargazer"))
        self.assertTrue(replay_result["ok"], replay_result)
        self.assertEqual([], replay_result["unknown_sample_modules"])
        self.assertTrue(admission_result["ok"], admission_result)
        self.assertEqual([], admission_result["strict_missing_replay_routes"])
        self.assertEqual([], admission_result["strict_missing_samples"])
        self.assertEqual([], admission_result["strict_missing_sample_families"])

    def test_pet_and_tower_replay_aliases_satisfy_strict_admission(self):
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"
        samples = json.loads(fixture_path.read_text(encoding="utf-8"))

        admission_result = module_manifest.validate_module_admission_contract(
            samples,
            strict_modules=("法宝", "闯塔"),
        )

        self.assertEqual("法宝", module_manifest.get_module_name_for_replay_module("pet"))
        self.assertEqual("闯塔", module_manifest.get_module_name_for_replay_module("tower"))
        self.assertTrue(admission_result["ok"], admission_result)
        self.assertEqual([], admission_result["strict_missing_replay_routes"])
        self.assertEqual([], admission_result["strict_missing_samples"])
        self.assertEqual([], admission_result["strict_missing_sample_families"])

    def test_module_admission_contract_reports_strict_sample_gap(self):
        samples = {
            "taiyi.yindao": {
                "source": "unit",
                "module": "taiyi",
                "family": "taiyi_yindao",
                "event_type": "message",
                "text": "你引动【水之道】，获得了 100点神识！",
            }
        }

        result = module_manifest.validate_module_admission_contract(
            samples,
            strict_modules=("太一", "阴罗宗", "不存在模块"),
        )

        self.assertFalse(result["ok"])
        self.assertEqual(["不存在模块"], result["strict_unknown_modules"])
        self.assertEqual(["阴罗宗"], result["strict_missing_samples"])

    def test_module_admission_contract_reports_family_sample_gap_without_failing(self):
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"
        samples = json.loads(fixture_path.read_text(encoding="utf-8"))

        result = module_manifest.validate_module_admission_contract(
            samples,
            strict_modules=("合欢宗",),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual([], result["strict_missing_samples"])
        self.assertEqual(
            ["合欢宗:hehuan_escape"],
            result["strict_missing_sample_families"],
        )

    def test_module_contract_summary_covers_all_modules_without_runtime_coupling(self):
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"
        samples = json.loads(fixture_path.read_text(encoding="utf-8"))

        summary = module_manifest.summarize_module_contracts(
            samples,
            strict_modules=("太一", "阴罗宗", "不存在模块"),
        )
        rows = {row["module"]: row for row in summary["modules"]}

        self.assertEqual(len(tuple(module_manifest.iter_module_manifests())), summary["totals"]["modules"])
        self.assertEqual([], [row["module"] for row in summary["modules"] if not row["observation_route"]])
        self.assertEqual(["不存在模块"], summary["unknown_strict_modules"])
        self.assertTrue(rows["太一"]["strict"])
        self.assertTrue(rows["阴罗宗"]["strict"])
        self.assertFalse(rows["灵树"]["strict"])
        self.assertTrue(rows["灵树"]["archived"])
        self.assertEqual("phase", rows["太一"]["duplicate_guard"])
        self.assertEqual(module_manifest.SEND_POLICY_PASSIVE_FIRST, rows["阴罗宗"]["send_policy"])
        self.assertEqual([], rows["太一"]["missing_sample_families"])
        self.assertEqual(module_manifest.READINESS_SAMPLE_COMPLETE, rows["阴罗宗"]["readiness"])
        self.assertIn("taiyi_yindao", rows["太一"]["covered_sample_families"])
        self.assertGreater(summary["totals"]["covered_sample_families"], 0)

    def test_module_readiness_backlog_classifies_existing_modules(self):
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"
        samples = json.loads(fixture_path.read_text(encoding="utf-8"))

        summary = module_manifest.summarize_module_readiness(
            samples,
            strict_modules=("灵树", "天星宗", "深度闭关", "玄骨考校"),
        )
        rows = {row["module"]: row for row in summary["modules"]}

        self.assertEqual(len(tuple(module_manifest.iter_module_manifests())), summary["totals"]["modules"])
        self.assertEqual(34, summary["totals"]["active_modules"])
        self.assertEqual(1, summary["totals"]["archived_modules"])
        self.assertEqual(81, summary["totals"]["reply_families"])
        self.assertEqual(4, summary["totals"]["archived_reply_families"])
        self.assertEqual(80, summary["totals"]["covered_sample_families"])
        self.assertEqual(1, summary["totals"]["missing_sample_families"])
        self.assertEqual(30, summary["totals"]["sample_complete_modules"])
        self.assertEqual(1, summary["totals"]["sample_partial_modules"])
        self.assertEqual(0, summary["totals"]["sample_missing_modules"])
        self.assertEqual(3, summary["totals"]["contract_only_modules"])
        self.assertEqual(module_manifest.READINESS_ARCHIVED, rows["灵树"]["readiness"])
        self.assertEqual(module_manifest.READINESS_SAMPLE_COMPLETE, rows["天星宗"]["readiness"])
        self.assertEqual(module_manifest.READINESS_SAMPLE_COMPLETE, rows["深度闭关"]["readiness"])
        self.assertEqual(module_manifest.READINESS_CONTRACT_ONLY, rows["玄骨考校"]["readiness"])
        self.assertEqual([], rows["灵树"]["missing_sample_families"])
        self.assertTrue(rows["灵树"]["archived"])
        self.assertEqual([], rows["天星宗"]["missing_sample_families"])
        self.assertEqual(["hehuan_escape"], rows["合欢宗"]["missing_sample_families"])
        self.assertTrue(rows["灵树"]["strict"])
        self.assertTrue(rows["玄骨考校"]["strict"])

    def test_report_only_feature_contracts_are_default_off_and_not_runtime_registered(self):
        result = module_manifest.validate_report_only_feature_contracts()

        self.assertTrue(result["ok"], result)
        self.assertEqual([], result["runtime_connected"])
        self.assertEqual([], result["primary_api_inputs"])
        contracts = {
            contract.feature_key: contract
            for contract in module_manifest.iter_report_only_feature_contracts()
        }
        self.assertEqual({"auto_repair", "search_node_api_fallback"}, set(contracts))
        self.assertIs(module_manifest.get_report_only_feature_contract("一键修理"), contracts["auto_repair"])
        self.assertIs(
            module_manifest.get_report_only_feature_contract("search_node_api_fallback"),
            contracts["search_node_api_fallback"],
        )
        self.assertIsNone(module_manifest.get_module_manifest("一键修理"))
        self.assertEqual("太一", contracts["search_node_api_fallback"].parent_module)
        for contract in contracts.values():
            self.assertEqual(module_manifest.MODULE_STAGE_REPORT_ONLY, contract.stage)
            self.assertFalse(contract.default_enabled)
            self.assertFalse(contract.manifest_registered)
            self.assertFalse(contract.scheduler_connected)
            self.assertFalse(contract.ui_connected)
            self.assertNotIn(module_manifest.INPUT_SOURCE_API_BACKUP, contract.primary_inputs)
            self.assertEqual(module_manifest.API_POLICY_BACKUP_ONLY, contract.api_policy)

    def test_module_admission_accepts_strict_report_only_contract_names(self):
        result = module_manifest.validate_module_admission_contract(
            strict_modules=("一键修理", "search_node_api_fallback"),
        )

        self.assertTrue(result["ok"], result)
        self.assertEqual([], result["strict_unknown_modules"])
        self.assertEqual(["search_node_api_fallback", "一键修理"], result["strict_report_only_modules"])

    def test_phaseful_modules_are_passive_first_last_resort_query(self):
        deep_retreat = module_manifest.get_module_manifest("深度闭关")
        yuanying = module_manifest.get_module_manifest("元婴")
        explore_rift = module_manifest.get_module_manifest("探寻裂缝")
        formation = module_manifest.get_module_manifest("周天星斗")

        self.assertEqual(module_manifest.SEND_POLICY_PASSIVE_FIRST, deep_retreat.send_policy)
        self.assertEqual(module_manifest.ACTIVE_QUERY_LAST_RESORT, deep_retreat.active_query_policy)
        self.assertEqual(module_manifest.SEND_POLICY_PASSIVE_FIRST, yuanying.send_policy)
        self.assertEqual(module_manifest.ACTIVE_QUERY_LAST_RESORT, yuanying.active_query_policy)
        self.assertEqual(module_manifest.SEND_POLICY_PASSIVE_FIRST, explore_rift.send_policy)
        self.assertEqual(module_manifest.ACTIVE_QUERY_LAST_RESORT, explore_rift.active_query_policy)
        self.assertEqual(module_manifest.SEND_POLICY_PASSIVE_FIRST, formation.send_policy)
        self.assertEqual(module_manifest.ACTIVE_QUERY_LAST_RESORT, formation.active_query_policy)

    def test_phaseful_and_passive_behavior_priority_policy(self):
        deep_retreat = module_manifest.get_behavior_spec("深度闭关")
        yuanying = module_manifest.get_behavior_spec("元婴")
        explore_rift = module_manifest.get_behavior_spec("探寻裂缝")
        formation = module_manifest.get_behavior_spec("周天星斗")
        guanxing_monitor = module_manifest.get_behavior_spec("观星监控")
        tree = module_manifest.get_behavior_spec("灵树")

        self.assertTrue(tree.archived)
        self.assertNotIn("灵树", [spec.name for spec in module_manifest.execution_order()])
        self.assertIn("灵树", [spec.name for spec in module_manifest.execution_order(include_archived=True)])
        self.assertTrue(deep_retreat.phaseful)
        self.assertTrue(yuanying.phaseful)
        self.assertEqual(module_manifest.PRIORITY_PHASEFUL, deep_retreat.priority)
        self.assertEqual(module_manifest.PRIORITY_PHASEFUL, yuanying.priority)
        self.assertEqual(module_manifest.PRIORITY_PASSIVE_CRITICAL, explore_rift.priority)
        self.assertEqual(module_manifest.PRIORITY_PASSIVE_CRITICAL, formation.priority)
        self.assertEqual(module_manifest.PRIORITY_PASSIVE, guanxing_monitor.priority)
        self.assertEqual(module_manifest.PRIORITY_NORMAL, tree.priority)
        self.assertLess(deep_retreat.priority, explore_rift.priority)
        self.assertLess(explore_rift.priority, guanxing_monitor.priority)


if __name__ == "__main__":
    unittest.main()
