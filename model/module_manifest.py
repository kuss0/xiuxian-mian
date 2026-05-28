from dataclasses import dataclass

from .config import MODULE_KEY_MAP, MODULE_NAMES


SEND_POLICY_PASSIVE_FIRST = "passive_first"
SEND_POLICY_OBSERVE_THEN_SEND = "observe_then_send"
ACTIVE_QUERY_FALLBACK_ONLY = "fallback_only"
ACTIVE_QUERY_LAST_RESORT = "last_resort"


@dataclass(frozen=True)
class ModuleManifest:
    name: str
    state_key: str = ""
    reply_families: tuple = ()
    send_policy: str = SEND_POLICY_OBSERVE_THEN_SEND
    active_query_policy: str = ACTIVE_QUERY_FALLBACK_ONLY
    duplicate_guard: str = "runtime"
    replay_required: bool = True


_MANIFESTS = (
    ModuleManifest("灵树", MODULE_KEY_MAP["灵树"], ("tree_panel", "tree_guard", "tree_harvest"), duplicate_guard="action_guard"),
    ModuleManifest("法宝", MODULE_KEY_MAP["法宝"], ("pet",), duplicate_guard="pending_reply"),
    ModuleManifest("温养器灵", MODULE_KEY_MAP["温养器灵"], ("pet_warm",), duplicate_guard="pending_reply"),
    ModuleManifest("器灵试炼", MODULE_KEY_MAP["器灵试炼"], ("pet_trial",), duplicate_guard="pending_reply"),
    ModuleManifest("放养", MODULE_KEY_MAP["放养"], ("ranch",), duplicate_guard="passive_result"),
    ModuleManifest("野外历练", MODULE_KEY_MAP["野外历练"], ("wild_training",), duplicate_guard="reply_msg_id"),
    ModuleManifest("观星台", MODULE_KEY_MAP["观星台"], ("stargazer_panel", "stargazer_guide", "stargazer_soothe", "stargazer_collect"), duplicate_guard="phase"),
    ModuleManifest("观星监控", MODULE_KEY_MAP["观星监控"], send_policy=SEND_POLICY_PASSIVE_FIRST, duplicate_guard="passive_broadcast"),
    ModuleManifest("观星", MODULE_KEY_MAP["观星"], ("guanxing_query", "guanxing_shift"), duplicate_guard="reply_msg_id"),
    ModuleManifest("登天阶", MODULE_KEY_MAP["登天阶"], ("tianti_status", "tianti_wenxin", "tianti_climb", "tianti_gangfeng"), duplicate_guard="reply_msg_id"),
    ModuleManifest("玄骨考校", MODULE_KEY_MAP["玄骨考校"], duplicate_guard="prompt_claim"),
    ModuleManifest("极阴祖师", MODULE_KEY_MAP["极阴祖师"], duplicate_guard="prompt_claim"),
    ModuleManifest("侍妾", MODULE_KEY_MAP["侍妾"], ("concubine_status", "concubine_greet", "concubine_gift", "concubine_dream", "concubine_fragment", "concubine_puzzle", "concubine_reacquire"), duplicate_guard="phase"),
    ModuleManifest("天机代卜", MODULE_KEY_MAP["天机代卜"], ("concubine_tianji",), duplicate_guard="chain_state"),
    ModuleManifest("共历心劫", MODULE_KEY_MAP["共历心劫"], ("concubine_heart",), duplicate_guard="round_state"),
    ModuleManifest("南陇侯", MODULE_KEY_MAP["南陇侯"], ("nanlong",), duplicate_guard="prompt_claim"),
    ModuleManifest("元婴", MODULE_KEY_MAP["元婴"], ("yuanying",), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, duplicate_guard="phaseful"),
    ModuleManifest("深度闭关", MODULE_KEY_MAP["深度闭关"], ("deep_retreat",), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, duplicate_guard="phaseful"),
    ModuleManifest("小世界", MODULE_KEY_MAP["小世界"], ("small_world_preach", "small_world_query", "small_world_manifest", "small_world_harvest", "small_world_refine"), duplicate_guard="phase"),
    ModuleManifest("点卯", MODULE_KEY_MAP["点卯"], ("checkin", "sect_teach"), duplicate_guard="daily_state"),
    ModuleManifest("闯塔", MODULE_KEY_MAP["闯塔"], ("tower",), duplicate_guard="daily_state"),
    ModuleManifest("第二元神", MODULE_KEY_MAP["第二元神"], ("second_soul_status", "second_soul_train", "second_soul_choice"), duplicate_guard="phase"),
    ModuleManifest("太一", MODULE_KEY_MAP["太一"], ("taiyi_yindao", "taiyi_node_search", "taiyi_node_define"), duplicate_guard="phase"),
    ModuleManifest("自动副本", MODULE_KEY_MAP["自动副本"], ("replica_join",), duplicate_guard="run_state"),
    ModuleManifest("储物袋", "", ("storage_bag", "storage_bag_listing", "storage_bag_buy", "storage_bag_gift"), duplicate_guard="transfer_state"),
)

MODULE_MANIFESTS = {manifest.name: manifest for manifest in _MANIFESTS}
REPLY_FAMILY_TO_MODULE = {
    family: manifest.name
    for manifest in _MANIFESTS
    for family in tuple(manifest.reply_families or ())
}


def get_module_manifest(name):
    return MODULE_MANIFESTS.get(str(name or "").strip())


def get_module_name_for_reply_family(family):
    return REPLY_FAMILY_TO_MODULE.get(str(family or "").strip(), "")


def iter_module_manifests():
    return tuple(_MANIFESTS)


def validate_module_manifest_coverage():
    missing_modules = [name for name in MODULE_NAMES if name not in MODULE_MANIFESTS]
    invalid_state_keys = [
        manifest.name
        for manifest in _MANIFESTS
        if manifest.name in MODULE_KEY_MAP and manifest.state_key != MODULE_KEY_MAP[manifest.name]
    ]
    seen_families = {}
    duplicate_reply_families = []
    for manifest in _MANIFESTS:
        for family in tuple(manifest.reply_families or ()):
            if family in seen_families:
                duplicate_reply_families.append(family)
            seen_families[family] = manifest.name
    return {
        "ok": not missing_modules and not invalid_state_keys and not duplicate_reply_families,
        "missing_modules": missing_modules,
        "invalid_state_keys": invalid_state_keys,
        "duplicate_reply_families": duplicate_reply_families,
    }
