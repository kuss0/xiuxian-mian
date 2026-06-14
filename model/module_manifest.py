from dataclasses import dataclass

from .config import MODULE_KEY_MAP, MODULE_NAMES


SEND_POLICY_PASSIVE_FIRST = "passive_first"
SEND_POLICY_OBSERVE_THEN_SEND = "observe_then_send"
ACTIVE_QUERY_FALLBACK_ONLY = "fallback_only"
ACTIVE_QUERY_LAST_RESORT = "last_resort"


class BehaviorPriority:
    PHASEFUL = 10
    PASSIVE_CRITICAL = 20
    PASSIVE = 30
    NORMAL = 50


PRIORITY_PHASEFUL = BehaviorPriority.PHASEFUL
PRIORITY_PASSIVE_CRITICAL = BehaviorPriority.PASSIVE_CRITICAL
PRIORITY_PASSIVE = BehaviorPriority.PASSIVE
PRIORITY_NORMAL = BehaviorPriority.NORMAL


@dataclass(frozen=True)
class ModuleManifest:
    name: str
    state_key: str = ""
    replay_modules: tuple = ()
    reply_families: tuple = ()
    send_policy: str = SEND_POLICY_OBSERVE_THEN_SEND
    active_query_policy: str = ACTIVE_QUERY_FALLBACK_ONLY
    duplicate_guard: str = "runtime"
    replay_required: bool = True
    workflow_names: tuple = ()


@dataclass(frozen=True)
class BehaviorSpec:
    name: str
    module: str
    state_key: str = ""
    triggers: tuple = ()
    reply_families: tuple = ()
    replay_modules: tuple = ()
    priority: int = PRIORITY_NORMAL
    send_policy: str = SEND_POLICY_OBSERVE_THEN_SEND
    active_query_policy: str = ACTIVE_QUERY_FALLBACK_ONLY
    duplicate_guard: str = "runtime"
    replay_required: bool = True
    workflow_names: tuple = ()
    registry_index: int = 0
    action_specs: tuple = ()
    phaseful: bool = False

    @classmethod
    def from_manifest(cls, manifest, registry_index=0):
        replay_modules = tuple(manifest.replay_modules or ())
        phaseful = manifest.duplicate_guard == "phaseful"
        if phaseful:
            priority = PRIORITY_PHASEFUL
        elif (
            manifest.send_policy == SEND_POLICY_PASSIVE_FIRST
            and manifest.active_query_policy == ACTIVE_QUERY_LAST_RESORT
        ):
            priority = PRIORITY_PASSIVE_CRITICAL
        elif manifest.send_policy == SEND_POLICY_PASSIVE_FIRST:
            priority = PRIORITY_PASSIVE
        else:
            priority = PRIORITY_NORMAL
        return cls(
            name=manifest.name,
            module=replay_modules[0] if replay_modules else manifest.name,
            state_key=manifest.state_key,
            reply_families=tuple(manifest.reply_families or ()),
            replay_modules=replay_modules,
            priority=priority,
            send_policy=manifest.send_policy,
            active_query_policy=manifest.active_query_policy,
            duplicate_guard=manifest.duplicate_guard,
            replay_required=manifest.replay_required,
            workflow_names=tuple(manifest.workflow_names or ()),
            registry_index=registry_index,
            phaseful=phaseful,
        )


_MANIFESTS = (
    ModuleManifest("灵树", MODULE_KEY_MAP["灵树"], reply_families=("tree_panel", "tree_pulse", "tree_guard", "tree_harvest"), replay_required=False, duplicate_guard="action_guard"),
    ModuleManifest("法宝", MODULE_KEY_MAP["法宝"], reply_families=("pet",), replay_required=False, duplicate_guard="pending_reply"),
    ModuleManifest("温养器灵", MODULE_KEY_MAP["温养器灵"], reply_families=("pet_warm",), replay_required=False, duplicate_guard="pending_reply"),
    ModuleManifest("器灵试炼", MODULE_KEY_MAP["器灵试炼"], reply_families=("pet_trial",), replay_required=False, duplicate_guard="pending_reply"),
    ModuleManifest("放养", MODULE_KEY_MAP["放养"], reply_families=("ranch",), replay_required=False, duplicate_guard="passive_result"),
    ModuleManifest("野外历练", MODULE_KEY_MAP["野外历练"], replay_modules=("wild_training",), reply_families=("wild_training",), duplicate_guard="reply_msg_id"),
    ModuleManifest("观星台", MODULE_KEY_MAP["观星台"], reply_families=("stargazer_panel", "stargazer_guide", "stargazer_soothe", "stargazer_collect"), replay_required=False, duplicate_guard="phase"),
    ModuleManifest("观星监控", MODULE_KEY_MAP["观星监控"], send_policy=SEND_POLICY_PASSIVE_FIRST, replay_required=False, duplicate_guard="passive_broadcast"),
    ModuleManifest("观星", MODULE_KEY_MAP["观星"], reply_families=("guanxing_query", "guanxing_shift"), replay_required=False, duplicate_guard="reply_msg_id"),
    ModuleManifest("周天星斗", MODULE_KEY_MAP["周天星斗"], replay_modules=("formation",), reply_families=("formation_start", "formation_assist"), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="run_state"),
    ModuleManifest("登天阶", MODULE_KEY_MAP["登天阶"], reply_families=("tianti_status", "tianti_wenxin", "tianti_climb", "tianti_gangfeng"), replay_required=False, duplicate_guard="reply_msg_id"),
    ModuleManifest("玄骨考校", MODULE_KEY_MAP["玄骨考校"], replay_required=False, duplicate_guard="prompt_claim"),
    ModuleManifest("极阴祖师", MODULE_KEY_MAP["极阴祖师"], replay_required=False, duplicate_guard="prompt_claim"),
    ModuleManifest("侍妾", MODULE_KEY_MAP["侍妾"], reply_families=("concubine_status", "concubine_greet", "concubine_gift", "concubine_dream", "concubine_fragment", "concubine_puzzle", "concubine_reacquire"), replay_required=False, duplicate_guard="phase", workflow_names=("concubine",)),
    ModuleManifest("天机代卜", MODULE_KEY_MAP["天机代卜"], reply_families=("concubine_tianji",), replay_required=False, duplicate_guard="chain_state"),
    ModuleManifest("共历心劫", MODULE_KEY_MAP["共历心劫"], reply_families=("concubine_heart",), replay_required=False, duplicate_guard="round_state"),
    ModuleManifest("侍妾远航", MODULE_KEY_MAP["侍妾远航"], replay_modules=("concubine_voyage",), reply_families=("concubine_voyage",), replay_required=False, duplicate_guard="voyage_state"),
    ModuleManifest("合欢宗", MODULE_KEY_MAP["合欢宗"], replay_modules=("hehuan",), reply_families=("hehuan_retreat", "hehuan_contract", "hehuan_dual", "hehuan_seal", "hehuan_escape"), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="passive_observation"),
    ModuleManifest("天星宗", MODULE_KEY_MAP["天星宗"], replay_modules=("tianxing",), reply_families=("tianxing_help", "tianxing_panel", "tianxing_observe", "tianxing_set_star", "tianxing_predict", "tianxing_change_fate", "tianxing_clear_calamity", "tianxing_modifier", "tianxing_retreat"), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="passive_observation"),
    ModuleManifest("阴罗宗", MODULE_KEY_MAP["阴罗宗"], replay_modules=("yinluo",), reply_families=("yinluo_guide", "yinluo_banner", "yinluo_blood_forest", "yinluo_demon_summon", "yinluo_convert", "yinluo_collect", "yinluo_refine", "yinluo_retreat", "yinluo_curse", "yinluo_possess"), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="passive_observation"),
    ModuleManifest("真仙试锋", MODULE_KEY_MAP["真仙试锋"], replay_modules=("world_boss",), reply_families=("world_boss",), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="event_state"),
    ModuleManifest("南陇侯", MODULE_KEY_MAP["南陇侯"], reply_families=("nanlong",), replay_required=False, duplicate_guard="prompt_claim"),
    ModuleManifest("元婴", MODULE_KEY_MAP["元婴"], replay_modules=("yuanying",), reply_families=("yuanying",), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, duplicate_guard="phaseful"),
    ModuleManifest("问道", MODULE_KEY_MAP["问道"], replay_modules=("wendao",), reply_families=("wendao",), replay_required=False, duplicate_guard="reply_msg_id"),
    ModuleManifest("深度闭关", MODULE_KEY_MAP["深度闭关"], replay_modules=("deep_retreat",), reply_families=("deep_retreat",), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, duplicate_guard="phaseful", workflow_names=("deep_retreat",)),
    ModuleManifest("小世界", MODULE_KEY_MAP["小世界"], replay_modules=("small_world",), reply_families=("small_world_preach", "small_world_relief", "small_world_query", "small_world_manifest", "small_world_harvest", "small_world_refine"), replay_required=False, duplicate_guard="phase"),
    ModuleManifest("卜筮问天", MODULE_KEY_MAP["卜筮问天"], replay_modules=("divination",), reply_families=("divination", "divination_exchange"), replay_required=False, duplicate_guard="reply_msg_id"),
    ModuleManifest("探寻裂缝", MODULE_KEY_MAP["探寻裂缝"], replay_modules=("explore_rift",), reply_families=("explore_rift",), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="reply_msg_id"),
    ModuleManifest("点卯", MODULE_KEY_MAP["点卯"], reply_families=("checkin",), replay_required=False, duplicate_guard="daily_state"),
    ModuleManifest("宗门传功", MODULE_KEY_MAP["宗门传功"], reply_families=("sect_teach",), replay_required=False, duplicate_guard="daily_state"),
    ModuleManifest("闯塔", MODULE_KEY_MAP["闯塔"], reply_families=("tower",), replay_required=False, duplicate_guard="daily_state"),
    ModuleManifest("第二元神", MODULE_KEY_MAP["第二元神"], reply_families=("second_soul_status", "second_soul_train", "second_soul_choice"), replay_required=False, duplicate_guard="phase"),
    ModuleManifest("太一", MODULE_KEY_MAP["太一"], replay_modules=("taiyi",), reply_families=("taiyi_yindao", "taiyi_node_search", "taiyi_node_define"), duplicate_guard="phase", workflow_names=("taiyi",)),
    ModuleManifest("自动副本", MODULE_KEY_MAP["自动副本"], replay_modules=("join_dungeon", "app_replica"), reply_families=("replica_join", "dungeon_join"), duplicate_guard="run_state", workflow_names=("dungeon_join",)),
    ModuleManifest("储物袋", "", replay_modules=("storage_bag",), reply_families=("storage_bag", "storage_bag_listing", "storage_bag_buy", "storage_bag_gift"), duplicate_guard="transfer_state", workflow_names=("storage_bag_transfer",)),
)

MODULE_MANIFESTS = {manifest.name: manifest for manifest in _MANIFESTS}
_BEHAVIOR_SPECS = tuple(
    BehaviorSpec.from_manifest(manifest, registry_index=index)
    for index, manifest in enumerate(_MANIFESTS)
)
BEHAVIOR_SPECS = {spec.name: spec for spec in _BEHAVIOR_SPECS}
REPLY_FAMILY_TO_MODULE = {
    family: manifest.name
    for manifest in _MANIFESTS
    for family in tuple(manifest.reply_families or ())
}
WORKFLOW_TO_MODULE = {
    workflow: manifest.name
    for manifest in _MANIFESTS
    for workflow in tuple(manifest.workflow_names or ())
}
REPLAY_MODULE_TO_MODULE = {
    replay_module: manifest.name
    for manifest in _MANIFESTS
    for replay_module in tuple(manifest.replay_modules or ())
}


def get_module_manifest(name):
    return MODULE_MANIFESTS.get(str(name or "").strip())


def get_behavior_spec(name):
    return BEHAVIOR_SPECS.get(str(name or "").strip())


def get_module_name_for_reply_family(family):
    return REPLY_FAMILY_TO_MODULE.get(str(family or "").strip(), "")


def get_module_name_for_workflow(workflow):
    return WORKFLOW_TO_MODULE.get(str(workflow or "").strip(), "")


def get_module_name_for_replay_module(replay_module):
    return REPLAY_MODULE_TO_MODULE.get(str(replay_module or "").strip(), "")


def iter_module_manifests():
    return tuple(_MANIFESTS)


def iter_behavior_specs():
    return tuple(_BEHAVIOR_SPECS)


def execution_order():
    return tuple(
        sorted(
            _BEHAVIOR_SPECS,
            key=lambda spec: (spec.priority, spec.registry_index, spec.name),
        )
    )


def validate_module_manifest_coverage():
    missing_modules = [name for name in MODULE_NAMES if name not in MODULE_MANIFESTS]
    invalid_state_keys = [
        manifest.name
        for manifest in _MANIFESTS
        if manifest.name in MODULE_KEY_MAP and manifest.state_key != MODULE_KEY_MAP[manifest.name]
    ]
    seen_families = {}
    duplicate_reply_families = []
    seen_replay_modules = {}
    duplicate_replay_modules = []
    seen_workflows = {}
    duplicate_workflows = []
    for manifest in _MANIFESTS:
        for replay_module in tuple(manifest.replay_modules or ()):
            if replay_module in seen_replay_modules:
                duplicate_replay_modules.append(replay_module)
            seen_replay_modules[replay_module] = manifest.name
        for family in tuple(manifest.reply_families or ()):
            if family in seen_families:
                duplicate_reply_families.append(family)
            seen_families[family] = manifest.name
        for workflow in tuple(manifest.workflow_names or ()):
            if workflow in seen_workflows:
                duplicate_workflows.append(workflow)
            seen_workflows[workflow] = manifest.name
    return {
        "ok": not missing_modules and not invalid_state_keys and not duplicate_replay_modules and not duplicate_reply_families and not duplicate_workflows,
        "missing_modules": missing_modules,
        "invalid_state_keys": invalid_state_keys,
        "duplicate_replay_modules": duplicate_replay_modules,
        "duplicate_reply_families": duplicate_reply_families,
        "duplicate_workflows": duplicate_workflows,
    }


def validate_behavior_spec_coverage():
    manifest_result = validate_module_manifest_coverage()
    seen_names = {}
    duplicate_names = []
    seen_families = {}
    duplicate_reply_families = []
    missing_manifests = []
    mismatched_registry_indexes = []
    for index, spec in enumerate(_BEHAVIOR_SPECS):
        if spec.name in seen_names:
            duplicate_names.append(spec.name)
        seen_names[spec.name] = True
        if spec.name not in MODULE_MANIFESTS:
            missing_manifests.append(spec.name)
        if spec.registry_index != index:
            mismatched_registry_indexes.append(spec.name)
        for family in tuple(spec.reply_families or ()):
            if family in seen_families:
                duplicate_reply_families.append(family)
            seen_families[family] = spec.name

    missing_specs = [manifest.name for manifest in _MANIFESTS if manifest.name not in BEHAVIOR_SPECS]
    return {
        "ok": (
            manifest_result["ok"]
            and not duplicate_names
            and not duplicate_reply_families
            and not missing_manifests
            and not missing_specs
            and not mismatched_registry_indexes
        ),
        "manifest_coverage": manifest_result,
        "duplicate_names": duplicate_names,
        "duplicate_reply_families": duplicate_reply_families,
        "missing_manifests": missing_manifests,
        "missing_specs": missing_specs,
        "mismatched_registry_indexes": mismatched_registry_indexes,
    }


def validate_replay_sample_coverage(samples):
    sample_items = samples.values() if isinstance(samples, dict) else samples
    sample_module_names = set()
    sample_families = set()
    missing_sample_sources = []
    missing_sample_modules = []
    missing_sample_families = []
    unknown_sample_modules = []
    unknown_sample_families = []
    sample_pairs = samples.items() if isinstance(samples, dict) else (
        (getattr(sample, "sample_id", ""), sample) for sample in sample_items
    )
    for sample_id, payload in sample_pairs:
        sample_source = ""
        sample_module = ""
        sample_family = ""
        if isinstance(payload, dict):
            sample_source = str(payload.get("source") or "").strip()
            sample_module = str(payload.get("module") or "").strip()
            sample_family = str(payload.get("family") or "").strip()
        else:
            sample_source = str(getattr(payload, "source", "") or "").strip()
            sample_module = str(getattr(payload, "module", "") or "").strip()
            sample_family = str(getattr(payload, "family", "") or "").strip()
        if not sample_source:
            missing_sample_sources.append(str(sample_id))
        if not sample_module:
            missing_sample_modules.append(str(sample_id))
        else:
            sample_module_names.add(sample_module)
            if not get_module_name_for_replay_module(sample_module):
                unknown_sample_modules.append(str(sample_id))
        if not sample_family:
            missing_sample_families.append(str(sample_id))
        else:
            sample_families.add(sample_family)
            if not get_module_name_for_reply_family(sample_family):
                unknown_sample_families.append(str(sample_id))

    missing_required_modules = []
    for manifest in _MANIFESTS:
        if not manifest.replay_required:
            continue
        has_module_sample = any(replay_module in sample_module_names for replay_module in tuple(manifest.replay_modules or ()))
        has_family_sample = any(family in sample_families for family in tuple(manifest.reply_families or ()))
        if not has_module_sample and not has_family_sample:
            missing_required_modules.append(manifest.name)

    return {
        "ok": (
            not missing_sample_sources
            and not missing_sample_modules
            and not missing_sample_families
            and not unknown_sample_modules
            and not unknown_sample_families
            and not missing_required_modules
        ),
        "missing_sample_sources": missing_sample_sources,
        "missing_sample_modules": missing_sample_modules,
        "missing_sample_families": missing_sample_families,
        "unknown_sample_modules": unknown_sample_modules,
        "unknown_sample_families": unknown_sample_families,
        "missing_required_modules": missing_required_modules,
    }


def summarize_replay_family_coverage(samples):
    sample_items = samples.values() if isinstance(samples, dict) else samples
    sample_families = set()
    for payload in sample_items:
        if isinstance(payload, dict):
            sample_family = str(payload.get("family") or "").strip()
        else:
            sample_family = str(getattr(payload, "family", "") or "").strip()
        if sample_family:
            sample_families.add(sample_family)

    modules = []
    all_missing = []
    covered_total = 0
    family_total = 0
    for manifest in _MANIFESTS:
        families = tuple(manifest.reply_families or ())
        if not families:
            continue
        covered = [family for family in families if family in sample_families]
        missing = [family for family in families if family not in sample_families]
        family_total += len(families)
        covered_total += len(covered)
        if missing:
            all_missing.extend({"module": manifest.name, "family": family} for family in missing)
        modules.append(
            {
                "module": manifest.name,
                "reply_families": list(families),
                "covered_families": covered,
                "missing_families": missing,
                "covered_count": len(covered),
                "total_count": len(families),
            }
        )

    return {
        "ok": not all_missing,
        "total_modules": len(modules),
        "total_families": family_total,
        "covered_families": covered_total,
        "missing_families": all_missing,
        "modules": modules,
    }
