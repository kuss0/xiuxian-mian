from dataclasses import dataclass

from .config import MODULE_KEY_MAP, MODULE_NAMES


SEND_POLICY_PASSIVE_FIRST = "passive_first"
SEND_POLICY_OBSERVE_THEN_SEND = "observe_then_send"
ACTIVE_QUERY_FALLBACK_ONLY = "fallback_only"
ACTIVE_QUERY_LAST_RESORT = "last_resort"
MODULE_STAGE_REPORT_ONLY = "report_only"
MODULE_STAGE_CONTRACTED = "contracted"
MODULE_STAGE_SEND_CAPABLE = "send_capable"
MODULE_STAGE_ARCHIVED = "archived"
INPUT_SOURCE_LOCAL_TEXT = "local_text"
INPUT_SOURCE_LOCAL_STATE = "local_state"
INPUT_SOURCE_REPLY_CONTEXT = "reply_context"
INPUT_SOURCE_EXISTING_SNAPSHOT = "existing_snapshot"
INPUT_SOURCE_API_BACKUP = "api_backup"
INPUT_SOURCE_OPERATOR_RPC = "operator_rpc"
INPUT_SOURCE_EXTERNAL_SERVICE = "external_service"
API_POLICY_NONE = "none"
API_POLICY_BACKUP_ONLY = "backup_only"
READINESS_SAMPLE_COMPLETE = "sample_complete"
READINESS_SAMPLE_PARTIAL = "sample_partial"
READINESS_SAMPLE_MISSING = "sample_missing"
READINESS_CONTRACT_ONLY = "contract_only"
READINESS_ARCHIVED = "archived"
RUST_FEATURE_READ_ONLY_QUERY = "read_only_query"
RUST_FEATURE_REPORT_OUTPUT = "report_output"
RUST_FEATURE_OPERATOR_TOOL = "operator_tool"
RUST_FEATURE_ACTIVE_ACTION = "active_action"
RUST_FEATURE_CONTROL_PLANE = "control_plane"
RUST_FEATURE_EXTERNAL_REPORT = "external_report"


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
    archived: bool = False
    archive_reason: str = ""


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
    archived: bool = False
    archive_reason: str = ""

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
            archived=bool(manifest.archived),
            archive_reason=manifest.archive_reason,
        )


@dataclass(frozen=True)
class ReportOnlyFeatureContract:
    name: str
    feature_key: str
    module_path: str
    decision_func: str
    parent_module: str = ""
    stage: str = MODULE_STAGE_REPORT_ONLY
    primary_inputs: tuple = ()
    backup_inputs: tuple = ()
    output_commands: tuple = ()
    api_policy: str = API_POLICY_BACKUP_ONLY
    default_enabled: bool = False
    manifest_registered: bool = False
    scheduler_connected: bool = False
    ui_connected: bool = False
    planned_ui_controls: tuple = ()
    notes: tuple = ()


@dataclass(frozen=True)
class RustAlignmentCandidate:
    name: str
    feature_key: str
    rust_command: str
    category: str
    stage: str = MODULE_STAGE_REPORT_ONLY
    target_surface: str = ""
    primary_inputs: tuple = ()
    backup_inputs: tuple = ()
    api_policy: str = API_POLICY_NONE
    default_enabled: bool = False
    scheduler_connected: bool = False
    ui_connected: bool = False
    recommended_default_path: bool = False
    notes: tuple = ()


_MANIFESTS = (
    ModuleManifest(
        "灵树",
        MODULE_KEY_MAP["灵树"],
        reply_families=("tree_panel", "tree_miniapp", "tree_pulse", "tree_guard", "tree_harvest"),
        replay_required=False,
        duplicate_guard="action_guard",
        archived=True,
        archive_reason="旧版灵树/定脉/采摘 automation 已归档；新灵树为 MiniApp 跳一跳/飞一飞 lab-only 候选，不接生产 scheduler。",
    ),
    ModuleManifest("法宝", MODULE_KEY_MAP["法宝"], replay_modules=("pet",), reply_families=("pet",), replay_required=False, duplicate_guard="pending_reply"),
    ModuleManifest("温养器灵", MODULE_KEY_MAP["温养器灵"], reply_families=("pet_warm",), replay_required=False, duplicate_guard="pending_reply"),
    ModuleManifest("器灵试炼", MODULE_KEY_MAP["器灵试炼"], reply_families=("pet_trial",), replay_required=False, duplicate_guard="pending_reply"),
    ModuleManifest("布下剑阵", MODULE_KEY_MAP["布下剑阵"], reply_families=("pet_formation",), replay_required=False, duplicate_guard="pending_reply"),
    ModuleManifest("放养", MODULE_KEY_MAP["放养"], reply_families=("ranch",), replay_required=False, duplicate_guard="passive_result"),
    ModuleManifest("野外历练", MODULE_KEY_MAP["野外历练"], replay_modules=("wild_training",), reply_families=("wild_training",), duplicate_guard="reply_msg_id"),
    ModuleManifest("观星台", MODULE_KEY_MAP["观星台"], replay_modules=("stargazer",), reply_families=("stargazer_panel", "stargazer_guide", "stargazer_soothe", "stargazer_collect"), replay_required=False, duplicate_guard="phase"),
    ModuleManifest("观星监控", MODULE_KEY_MAP["观星监控"], send_policy=SEND_POLICY_PASSIVE_FIRST, replay_required=False, duplicate_guard="passive_broadcast"),
    ModuleManifest("观星", MODULE_KEY_MAP["观星"], reply_families=("guanxing_query", "guanxing_shift"), replay_required=False, duplicate_guard="reply_msg_id"),
    ModuleManifest("MiniApp合集", "", replay_modules=("miniapp",), send_policy=SEND_POLICY_OBSERVE_THEN_SEND, replay_required=False, duplicate_guard="control_plane"),
    ModuleManifest("天机试炼", "", replay_modules=("trial",), send_policy=SEND_POLICY_OBSERVE_THEN_SEND, replay_required=False, duplicate_guard="batch_state"),
    ModuleManifest("周天星斗", MODULE_KEY_MAP["周天星斗"], replay_modules=("formation",), reply_families=("formation_start", "formation_assist"), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="run_state"),
    ModuleManifest("登天阶", MODULE_KEY_MAP["登天阶"], replay_modules=("tianti",), reply_families=("tianti_status", "tianti_wenxin", "tianti_climb", "tianti_gangfeng"), replay_required=False, duplicate_guard="reply_msg_id"),
    ModuleManifest("玄骨考校", MODULE_KEY_MAP["玄骨考校"], replay_required=False, duplicate_guard="prompt_claim"),
    ModuleManifest("极阴祖师", MODULE_KEY_MAP["极阴祖师"], replay_required=False, duplicate_guard="prompt_claim"),
    ModuleManifest("侍妾", MODULE_KEY_MAP["侍妾"], reply_families=("concubine_status", "concubine_greet", "concubine_gift", "concubine_dream", "concubine_fragment", "concubine_puzzle", "concubine_reacquire"), replay_required=False, duplicate_guard="phase", workflow_names=("concubine",)),
    ModuleManifest("天机代卜", MODULE_KEY_MAP["天机代卜"], reply_families=("concubine_tianji",), replay_required=False, duplicate_guard="chain_state"),
    ModuleManifest("共历心劫", MODULE_KEY_MAP["共历心劫"], reply_families=("concubine_heart",), replay_required=False, duplicate_guard="round_state"),
    ModuleManifest("侍妾远航", MODULE_KEY_MAP["侍妾远航"], replay_modules=("concubine_voyage",), reply_families=("concubine_voyage",), replay_required=False, duplicate_guard="voyage_state"),
    ModuleManifest("合欢宗", MODULE_KEY_MAP["合欢宗"], replay_modules=("hehuan",), reply_families=("hehuan_retreat", "hehuan_contract", "hehuan_dual", "hehuan_seal", "hehuan_escape"), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="passive_observation"),
    ModuleManifest("天星宗", MODULE_KEY_MAP["天星宗"], replay_modules=("tianxing",), reply_families=("tianxing_help", "tianxing_panel", "tianxing_observe", "tianxing_set_star", "tianxing_predict", "tianxing_change_fate", "tianxing_clear_calamity", "tianxing_modifier", "tianxing_retreat", "tianxing_retreat_farm", "tianxing_craft_farm"), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="passive_observation"),
    ModuleManifest("阴罗宗", MODULE_KEY_MAP["阴罗宗"], replay_modules=("yinluo",), reply_families=("yinluo_guide", "yinluo_banner", "yinluo_blood_forest", "yinluo_demon_summon", "yinluo_daily_sacrifice", "yinluo_convert", "yinluo_collect", "yinluo_refine", "yinluo_soothe", "yinluo_retreat", "yinluo_curse", "yinluo_possess"), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="passive_observation"),
    ModuleManifest("慕兰烽烟", MODULE_KEY_MAP["慕兰烽烟"], replay_modules=("mulan",), reply_families=("mulan_panel", "mulan_collect", "mulan_judge", "mulan_publish", "mulan_support"), replay_required=False, duplicate_guard="phase"),
    ModuleManifest("婉心封魂", MODULE_KEY_MAP["婉心封魂"], replay_modules=("wanxin",), reply_families=("wanxin_panel", "wanxin_visit", "wanxin_protect", "wanxin_deduce", "wanxin_commission", "wanxin_accept", "wanxin_assist_identify", "wanxin_assist_banner", "wanxin_assist_strip"), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="passive_observation"),
    ModuleManifest("真仙试锋", MODULE_KEY_MAP["真仙试锋"], replay_modules=("world_boss",), reply_families=("world_boss",), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="event_state"),
    ModuleManifest("南陇侯", MODULE_KEY_MAP["南陇侯"], reply_families=("nanlong",), replay_required=False, duplicate_guard="prompt_claim"),
    ModuleManifest("元婴", MODULE_KEY_MAP["元婴"], replay_modules=("yuanying",), reply_families=("yuanying",), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, duplicate_guard="phaseful"),
    ModuleManifest("问道", MODULE_KEY_MAP["问道"], replay_modules=("wendao",), reply_families=("wendao",), replay_required=False, duplicate_guard="reply_msg_id"),
    ModuleManifest("斗法", MODULE_KEY_MAP["斗法"], replay_modules=("duel",), reply_families=("duel",), replay_required=False, duplicate_guard="reply_msg_id"),
    ModuleManifest("灵溪垂钓", MODULE_KEY_MAP["灵溪垂钓"], replay_modules=("fishing",), reply_families=("fishing",), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="reply_msg_id"),
    ModuleManifest("深度闭关", MODULE_KEY_MAP["深度闭关"], replay_modules=("deep_retreat",), reply_families=("deep_retreat",), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, duplicate_guard="phaseful", workflow_names=("deep_retreat",)),
    ModuleManifest("小世界", MODULE_KEY_MAP["小世界"], replay_modules=("small_world",), reply_families=("small_world_preach", "small_world_relief", "small_world_query", "small_world_manifest", "small_world_harvest", "small_world_refine", "small_world_barrier"), replay_required=False, duplicate_guard="phase"),
    ModuleManifest("卜筮问天", MODULE_KEY_MAP["卜筮问天"], replay_modules=("divination",), reply_families=("divination", "divination_exchange"), replay_required=False, duplicate_guard="reply_msg_id"),
    ModuleManifest("探寻裂缝", MODULE_KEY_MAP["探寻裂缝"], replay_modules=("explore_rift",), reply_families=("explore_rift",), send_policy=SEND_POLICY_PASSIVE_FIRST, active_query_policy=ACTIVE_QUERY_LAST_RESORT, replay_required=False, duplicate_guard="reply_msg_id"),
    ModuleManifest("点卯", MODULE_KEY_MAP["点卯"], reply_families=("checkin",), replay_required=False, duplicate_guard="daily_state"),
    ModuleManifest("宗门传功", MODULE_KEY_MAP["宗门传功"], reply_families=("sect_teach",), replay_required=False, duplicate_guard="daily_state"),
    ModuleManifest("闯塔", MODULE_KEY_MAP["闯塔"], replay_modules=("tower",), reply_families=("tower",), replay_required=False, duplicate_guard="daily_state"),
    ModuleManifest(
        "第二元神",
        MODULE_KEY_MAP["第二元神"],
        reply_families=(
            "second_soul_status",
            "second_soul_train",
            "second_soul_choice",
            "second_soul_purge",
            "second_soul_demon_status",
        ),
        replay_required=False,
        duplicate_guard="phase",
    ),
    ModuleManifest("太一", MODULE_KEY_MAP["太一"], replay_modules=("taiyi",), reply_families=("taiyi_yindao", "taiyi_node_search", "taiyi_node_define"), duplicate_guard="phase", workflow_names=("taiyi",)),
    ModuleManifest("自动副本", MODULE_KEY_MAP["自动副本"], replay_modules=("join_dungeon", "app_replica"), reply_families=("replica_join", "dungeon_join"), duplicate_guard="run_state", workflow_names=("dungeon_join",)),
    ModuleManifest("储物袋", "", replay_modules=("storage_bag",), reply_families=("storage_bag", "storage_bag_listing", "storage_bag_buy", "storage_bag_gift"), duplicate_guard="transfer_state", workflow_names=("storage_bag_transfer",)),
)

_REPORT_ONLY_FEATURE_CONTRACTS = (
    ReportOnlyFeatureContract(
        name="一键修理",
        feature_key="auto_repair",
        module_path="model.features.auto_repair",
        decision_func="decide_auto_repair",
        primary_inputs=(INPUT_SOURCE_LOCAL_STATE, INPUT_SOURCE_EXISTING_SNAPSHOT),
        backup_inputs=(INPUT_SOURCE_API_BACKUP,),
        output_commands=(".一键修理",),
        notes=(
            "evaluates already available durability/material state only",
            "not registered as a runtime module",
        ),
    ),
    ReportOnlyFeatureContract(
        name="搜寻节点备用门禁",
        feature_key="search_node_api_fallback",
        module_path="model.features.search_node",
        decision_func="decide_search_node_api_fallback",
        parent_module="太一",
        primary_inputs=(INPUT_SOURCE_LOCAL_STATE, INPUT_SOURCE_EXISTING_SNAPSHOT),
        backup_inputs=(INPUT_SOURCE_API_BACKUP,),
        output_commands=(".搜寻节点",),
        notes=(
            "Taiyi text-first state machine remains the owner",
            "API snapshot is backup evidence only",
        ),
    ),
)

_RUST_ALIGNMENT_CANDIDATES = (
    RustAlignmentCandidate(
        name="本地状态多视图",
        feature_key="local_status_views",
        rust_command="status root|sect|dongfu|companion|dungeon|smallworld",
        category=RUST_FEATURE_READ_ONLY_QUERY,
        target_surface="只读状态报告/miniweb 汇总",
        primary_inputs=(INPUT_SOURCE_LOCAL_TEXT, INPUT_SOURCE_LOCAL_STATE, INPUT_SOURCE_EXISTING_SNAPSHOT),
        recommended_default_path=True,
        notes=(
            "absorb the multi-view report shape without active fetch",
            "missing fields must render as unknown/no snapshot instead of querying live",
        ),
    ),
    RustAlignmentCandidate(
        name="储物袋本地盘点",
        feature_key="storage_bag_local_find",
        rust_command="inventory list|find",
        category=RUST_FEATURE_READ_ONLY_QUERY,
        target_surface="储物袋只读盘点/物资统计",
        primary_inputs=(INPUT_SOURCE_LOCAL_TEXT, INPUT_SOURCE_LOCAL_STATE, INPUT_SOURCE_EXISTING_SNAPSHOT),
        backup_inputs=(INPUT_SOURCE_API_BACKUP,),
        api_policy=API_POLICY_BACKUP_ONLY,
        recommended_default_path=True,
        notes=(
            "query local text/cache first",
            "API means already available manual UI/API cache only, never default live polling",
        ),
    ),
    RustAlignmentCandidate(
        name="跨身份物资调度",
        feature_key="inventory_transfer_planner",
        rust_command="inventory give|transfer|advance-transfer",
        category=RUST_FEATURE_ACTIVE_ACTION,
        target_surface="物资调度/交易规划",
        primary_inputs=(INPUT_SOURCE_LOCAL_TEXT, INPUT_SOURCE_LOCAL_STATE, INPUT_SOURCE_EXISTING_SNAPSHOT),
        notes=(
            "planner output is a command plan, not a pure query",
            "must stay explicit and user-driven; do not treat as a passive read surface",
        ),
    ),
    RustAlignmentCandidate(
        name="原始快照出口",
        feature_key="local_getdata_snapshot",
        rust_command="getdata <target>",
        category=RUST_FEATURE_READ_ONLY_QUERY,
        target_surface="只读诊断报告",
        primary_inputs=(INPUT_SOURCE_EXISTING_SNAPSHOT,),
        recommended_default_path=True,
        notes=(
            "operator-visible raw local snapshot for parser disputes",
            "does not refresh or mutate snapshot state",
        ),
    ),
    RustAlignmentCandidate(
        name="氪金合计显式补抓",
        feature_key="spent_status_explicit_fetch",
        rust_command="status spent [target] [--simple] [--fetch]",
        category=RUST_FEATURE_READ_ONLY_QUERY,
        target_surface="只读统计报告",
        primary_inputs=(INPUT_SOURCE_EXISTING_SNAPSHOT,),
        backup_inputs=(INPUT_SOURCE_API_BACKUP,),
        api_policy=API_POLICY_BACKUP_ONLY,
        notes=(
            "local snapshot total is safe",
            "fetch-like behavior must stay explicit operator action, not a default path",
        ),
    ),
    RustAlignmentCandidate(
        name="副本外部上报",
        feature_key="replica_external_upload_query",
        rust_command="dungeon upload-query",
        category=RUST_FEATURE_EXTERNAL_REPORT,
        target_surface="外部聚合服务/日志群按钮候选",
        primary_inputs=(INPUT_SOURCE_LOCAL_STATE, INPUT_SOURCE_EXISTING_SNAPSHOT, INPUT_SOURCE_EXTERNAL_SERVICE),
        notes=(
            "not a local query surface",
            "only consider after local replica reports and privacy boundaries are explicit",
        ),
    ),
    RustAlignmentCandidate(
        name="TG 身份互查",
        feature_key="whois_peer_lookup",
        rust_command="whois <id|@username>",
        category=RUST_FEATURE_OPERATOR_TOOL,
        target_surface="运维诊断",
        primary_inputs=(INPUT_SOURCE_OPERATOR_RPC,),
        notes=(
            "online Telegram RPC, useful for sender allowlist audits",
            "not part of game automation or local text-first module flow",
        ),
    ),
    RustAlignmentCandidate(
        name="小程序初始化诊断",
        feature_key="miniapp_init_data",
        rust_command="miniapp <link|@bot|bot>",
        category=RUST_FEATURE_OPERATOR_TOOL,
        target_surface="运维诊断",
        primary_inputs=(INPUT_SOURCE_OPERATOR_RPC,),
        notes=(
            "manual-only tool for webview/init-data diagnostics",
            "must not become a scheduler input",
        ),
    ),
    RustAlignmentCandidate(
        name="按钮点击诊断",
        feature_key="inline_click_diagnostic",
        rust_command="click <msg_id> (--text <s>|--rc <row> <col>)",
        category=RUST_FEATURE_ACTIVE_ACTION,
        target_surface="运维手动动作",
        primary_inputs=(INPUT_SOURCE_REPLY_CONTEXT, INPUT_SOURCE_OPERATOR_RPC),
        notes=(
            "active Telegram callback action",
            "keep manual/operator-only unless a module has explicit button contract tests",
        ),
    ),
    RustAlignmentCandidate(
        name="控制面命令组",
        feature_key="operator_control_commands",
        rust_command="delay|pacer|reload|sendby|startup|tglog|quizbank",
        category=RUST_FEATURE_CONTROL_PLANE,
        target_surface="运维控制面",
        primary_inputs=(INPUT_SOURCE_LOCAL_STATE, INPUT_SOURCE_OPERATOR_RPC),
        notes=(
            "useful as organization reference for command registry",
            "not a candidate for default game automation",
        ),
    ),
)

MODULE_MANIFESTS = {manifest.name: manifest for manifest in _MANIFESTS}
REPORT_ONLY_FEATURE_CONTRACTS = {
    contract.name: contract for contract in _REPORT_ONLY_FEATURE_CONTRACTS
}
REPORT_ONLY_FEATURE_CONTRACTS_BY_KEY = {
    contract.feature_key: contract for contract in _REPORT_ONLY_FEATURE_CONTRACTS
}
RUST_ALIGNMENT_CANDIDATES = {
    candidate.name: candidate for candidate in _RUST_ALIGNMENT_CANDIDATES
}
RUST_ALIGNMENT_CANDIDATES_BY_KEY = {
    candidate.feature_key: candidate for candidate in _RUST_ALIGNMENT_CANDIDATES
}
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
REPLY_FAMILY_TO_MODULE.update({
    "heavenly_pardon": "全局安全",
})
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

_PASSIVE_OBSERVATION_DUPLICATE_GUARDS = {
    "event_state",
    "passive_broadcast",
    "passive_observation",
    "passive_result",
    "prompt_claim",
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
    module = str(replay_module or "").strip()
    return REPLAY_MODULE_TO_MODULE.get(module, "") or (module if module in MODULE_MANIFESTS else "")


def is_module_archived(name):
    manifest = get_module_manifest(name)
    return bool(manifest and manifest.archived)


def is_reply_family_archived(family):
    module_name = get_module_name_for_reply_family(family)
    return is_module_archived(module_name)


def iter_module_manifests(include_archived=True):
    if include_archived:
        return tuple(_MANIFESTS)
    return tuple(manifest for manifest in _MANIFESTS if not manifest.archived)


def iter_archived_module_manifests():
    return tuple(manifest for manifest in _MANIFESTS if manifest.archived)


def iter_report_only_feature_contracts():
    return tuple(_REPORT_ONLY_FEATURE_CONTRACTS)


def get_report_only_feature_contract(name_or_key):
    key = str(name_or_key or "").strip()
    return REPORT_ONLY_FEATURE_CONTRACTS.get(key) or REPORT_ONLY_FEATURE_CONTRACTS_BY_KEY.get(key)


def iter_rust_alignment_candidates():
    return tuple(_RUST_ALIGNMENT_CANDIDATES)


def get_rust_alignment_candidate(name_or_key):
    key = str(name_or_key or "").strip()
    return RUST_ALIGNMENT_CANDIDATES.get(key) or RUST_ALIGNMENT_CANDIDATES_BY_KEY.get(key)


def iter_behavior_specs(include_archived=True):
    if include_archived:
        return tuple(_BEHAVIOR_SPECS)
    return tuple(spec for spec in _BEHAVIOR_SPECS if not spec.archived)


def execution_order(include_archived=False):
    return tuple(
        sorted(
            iter_behavior_specs(include_archived=include_archived),
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
    archived_replay_required = []
    for manifest in _MANIFESTS:
        if manifest.archived and manifest.replay_required:
            archived_replay_required.append(manifest.name)
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
        "ok": (
            not missing_modules
            and not invalid_state_keys
            and not duplicate_replay_modules
            and not duplicate_reply_families
            and not duplicate_workflows
            and not archived_replay_required
        ),
        "missing_modules": missing_modules,
        "invalid_state_keys": invalid_state_keys,
        "duplicate_replay_modules": duplicate_replay_modules,
        "duplicate_reply_families": duplicate_reply_families,
        "duplicate_workflows": duplicate_workflows,
        "archived_replay_required": archived_replay_required,
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


def _sample_modules_and_families(samples):
    sample_items = samples.values() if isinstance(samples, dict) else samples
    modules = set()
    families = set()
    for payload in sample_items:
        if isinstance(payload, dict):
            module = str(payload.get("module") or "").strip()
            family = str(payload.get("family") or "").strip()
        else:
            module = str(getattr(payload, "module", "") or "").strip()
            family = str(getattr(payload, "family", "") or "").strip()
        if module:
            modules.add(module)
        if family:
            families.add(family)
    return modules, families


def _has_observation_route(manifest):
    return bool(
        tuple(manifest.reply_families or ())
        or tuple(manifest.replay_modules or ())
        or tuple(manifest.workflow_names or ())
        or manifest.duplicate_guard in _PASSIVE_OBSERVATION_DUPLICATE_GUARDS
    )


def _report_only_contract_names_and_keys():
    names = {contract.name for contract in _REPORT_ONLY_FEATURE_CONTRACTS}
    keys = {contract.feature_key for contract in _REPORT_ONLY_FEATURE_CONTRACTS}
    return names | keys


def _rust_alignment_candidate_names_and_keys():
    names = {candidate.name for candidate in _RUST_ALIGNMENT_CANDIDATES}
    keys = {candidate.feature_key for candidate in _RUST_ALIGNMENT_CANDIDATES}
    return names | keys


def validate_report_only_feature_contracts():
    duplicate_names = []
    duplicate_keys = []
    missing_fields = []
    invalid_stage = []
    runtime_connected = []
    primary_api_inputs = []
    backup_api_policy_mismatch = []
    unknown_parent_modules = []
    seen_names = set()
    seen_keys = set()

    for contract in _REPORT_ONLY_FEATURE_CONTRACTS:
        if contract.name in seen_names:
            duplicate_names.append(contract.name)
        seen_names.add(contract.name)
        if contract.feature_key in seen_keys:
            duplicate_keys.append(contract.feature_key)
        seen_keys.add(contract.feature_key)

        for field_name in ("name", "feature_key", "module_path", "decision_func"):
            if not str(getattr(contract, field_name, "") or "").strip():
                missing_fields.append(f"{contract.name or contract.feature_key}:{field_name}")
        if not tuple(contract.primary_inputs or ()):
            missing_fields.append(f"{contract.name}:primary_inputs")
        if contract.stage != MODULE_STAGE_REPORT_ONLY:
            invalid_stage.append(contract.name)
        if contract.default_enabled or contract.manifest_registered or contract.scheduler_connected or contract.ui_connected:
            runtime_connected.append(contract.name)
        if INPUT_SOURCE_API_BACKUP in tuple(contract.primary_inputs or ()):
            primary_api_inputs.append(contract.name)
        if INPUT_SOURCE_API_BACKUP in tuple(contract.backup_inputs or ()) and contract.api_policy != API_POLICY_BACKUP_ONLY:
            backup_api_policy_mismatch.append(contract.name)
        if contract.parent_module and contract.parent_module not in MODULE_MANIFESTS:
            unknown_parent_modules.append(contract.name)

    return {
        "ok": (
            not duplicate_names
            and not duplicate_keys
            and not missing_fields
            and not invalid_stage
            and not runtime_connected
            and not primary_api_inputs
            and not backup_api_policy_mismatch
            and not unknown_parent_modules
        ),
        "duplicate_names": duplicate_names,
        "duplicate_keys": duplicate_keys,
        "missing_fields": missing_fields,
        "invalid_stage": invalid_stage,
        "runtime_connected": runtime_connected,
        "primary_api_inputs": primary_api_inputs,
        "backup_api_policy_mismatch": backup_api_policy_mismatch,
        "unknown_parent_modules": unknown_parent_modules,
    }


def validate_rust_alignment_candidates(known_external=()):
    duplicate_names = []
    duplicate_keys = []
    missing_fields = []
    invalid_stage = []
    runtime_connected = []
    primary_api_inputs = []
    backup_api_policy_mismatch = []
    recommended_non_readonly = []
    recommended_operator_inputs = []
    seen_names = set()
    seen_keys = set()

    allowed_categories = {
        RUST_FEATURE_READ_ONLY_QUERY,
        RUST_FEATURE_REPORT_OUTPUT,
        RUST_FEATURE_OPERATOR_TOOL,
        RUST_FEATURE_ACTIVE_ACTION,
        RUST_FEATURE_CONTROL_PLANE,
        RUST_FEATURE_EXTERNAL_REPORT,
    }
    default_path_categories = {
        RUST_FEATURE_READ_ONLY_QUERY,
        RUST_FEATURE_REPORT_OUTPUT,
    }

    for candidate in _RUST_ALIGNMENT_CANDIDATES:
        if candidate.name in seen_names:
            duplicate_names.append(candidate.name)
        seen_names.add(candidate.name)
        if candidate.feature_key in seen_keys:
            duplicate_keys.append(candidate.feature_key)
        seen_keys.add(candidate.feature_key)

        for field_name in ("name", "feature_key", "rust_command", "category", "target_surface"):
            if not str(getattr(candidate, field_name, "") or "").strip():
                missing_fields.append(f"{candidate.name or candidate.feature_key}:{field_name}")
        if candidate.category not in allowed_categories:
            missing_fields.append(f"{candidate.name}:category")
        if candidate.stage != MODULE_STAGE_REPORT_ONLY:
            invalid_stage.append(candidate.name)
        if candidate.default_enabled or candidate.scheduler_connected or candidate.ui_connected:
            runtime_connected.append(candidate.name)
        if INPUT_SOURCE_API_BACKUP in tuple(candidate.primary_inputs or ()):
            primary_api_inputs.append(candidate.name)
        if INPUT_SOURCE_API_BACKUP in tuple(candidate.backup_inputs or ()) and candidate.api_policy != API_POLICY_BACKUP_ONLY:
            backup_api_policy_mismatch.append(candidate.name)
        if candidate.recommended_default_path and candidate.category not in default_path_categories:
            recommended_non_readonly.append(candidate.name)
        if candidate.recommended_default_path and INPUT_SOURCE_OPERATOR_RPC in tuple(candidate.primary_inputs or ()):
            recommended_operator_inputs.append(candidate.name)

    return {
        "ok": (
            not duplicate_names
            and not duplicate_keys
            and not missing_fields
            and not invalid_stage
            and not runtime_connected
            and not primary_api_inputs
            and not backup_api_policy_mismatch
            and not recommended_non_readonly
            and not recommended_operator_inputs
        ),
        "duplicate_names": duplicate_names,
        "duplicate_keys": duplicate_keys,
        "missing_fields": missing_fields,
        "invalid_stage": invalid_stage,
        "runtime_connected": runtime_connected,
        "primary_api_inputs": primary_api_inputs,
        "backup_api_policy_mismatch": backup_api_policy_mismatch,
        "recommended_non_readonly": recommended_non_readonly,
        "recommended_operator_inputs": recommended_operator_inputs,
    }


def summarize_rust_alignment_candidates(strict_modules=(), known_external=()):
    strict = {str(name or "").strip() for name in tuple(strict_modules or ()) if str(name or "").strip()}
    external = {str(name or "").strip() for name in tuple(known_external or ()) if str(name or "").strip()}
    rows = []
    by_category = {}
    recommended = 0
    backup_api = 0
    operator_or_active = 0
    for candidate in _RUST_ALIGNMENT_CANDIDATES:
        by_category[candidate.category] = by_category.get(candidate.category, 0) + 1
        if candidate.recommended_default_path:
            recommended += 1
        if INPUT_SOURCE_API_BACKUP in tuple(candidate.backup_inputs or ()):
            backup_api += 1
        if candidate.category in {RUST_FEATURE_OPERATOR_TOOL, RUST_FEATURE_ACTIVE_ACTION, RUST_FEATURE_CONTROL_PLANE}:
            operator_or_active += 1
        rows.append(
            {
                "name": candidate.name,
                "feature_key": candidate.feature_key,
                "rust_command": candidate.rust_command,
                "category": candidate.category,
                "stage": candidate.stage,
                "target_surface": candidate.target_surface,
                "primary_inputs": list(candidate.primary_inputs or ()),
                "backup_inputs": list(candidate.backup_inputs or ()),
                "api_policy": candidate.api_policy,
                "default_enabled": bool(candidate.default_enabled),
                "scheduler_connected": bool(candidate.scheduler_connected),
                "ui_connected": bool(candidate.ui_connected),
                "recommended_default_path": bool(candidate.recommended_default_path),
                "notes": list(candidate.notes or ()),
                "strict": candidate.name in strict or candidate.feature_key in strict,
            }
        )

    known = _rust_alignment_candidate_names_and_keys()
    return {
        "candidates": rows,
        "totals": {
            "candidates": len(rows),
            "recommended_default_path": recommended,
            "backup_api_candidates": backup_api,
            "operator_or_active_candidates": operator_or_active,
            "categories": by_category,
            "strict_candidates": len(strict & known),
        },
        "unknown_strict_candidates": sorted(strict - known - external),
        "validation": validate_rust_alignment_candidates(known_external=external),
    }


def summarize_report_only_feature_contracts(strict_modules=()):
    strict = {str(name or "").strip() for name in tuple(strict_modules or ()) if str(name or "").strip()}
    rows = []
    backup_api = 0
    command_candidates = 0
    for contract in _REPORT_ONLY_FEATURE_CONTRACTS:
        is_strict = contract.name in strict or contract.feature_key in strict
        has_backup_api = INPUT_SOURCE_API_BACKUP in tuple(contract.backup_inputs or ())
        if has_backup_api:
            backup_api += 1
        if tuple(contract.output_commands or ()):
            command_candidates += 1
        rows.append(
            {
                "name": contract.name,
                "feature_key": contract.feature_key,
                "module_path": contract.module_path,
                "decision_func": contract.decision_func,
                "parent_module": contract.parent_module,
                "stage": contract.stage,
                "primary_inputs": list(contract.primary_inputs or ()),
                "backup_inputs": list(contract.backup_inputs or ()),
                "output_commands": list(contract.output_commands or ()),
                "api_policy": contract.api_policy,
                "default_enabled": bool(contract.default_enabled),
                "manifest_registered": bool(contract.manifest_registered),
                "scheduler_connected": bool(contract.scheduler_connected),
                "ui_connected": bool(contract.ui_connected),
                "planned_ui_controls": list(contract.planned_ui_controls or ()),
                "notes": list(contract.notes or ()),
                "strict": is_strict,
            }
        )

    known = _report_only_contract_names_and_keys()
    return {
        "modules": rows,
        "totals": {
            "modules": len(rows),
            "backup_api_modules": backup_api,
            "command_candidate_modules": command_candidates,
            "strict_modules": len(strict & known),
        },
        "unknown_strict_modules": sorted(strict - known),
        "validation": validate_report_only_feature_contracts(),
    }


def _module_readiness(reply_families, covered, missing):
    if not tuple(reply_families or ()):
        return READINESS_CONTRACT_ONLY
    if not missing:
        return READINESS_SAMPLE_COMPLETE
    if covered:
        return READINESS_SAMPLE_PARTIAL
    return READINESS_SAMPLE_MISSING


def _module_next_action(readiness, missing):
    if readiness == READINESS_ARCHIVED:
        return "archived; keep legacy family mapping only, do not add samples or reconnect sends"
    if readiness == READINESS_SAMPLE_COMPLETE:
        return "keep replay tests in scope before parser or handler changes"
    if readiness == READINESS_SAMPLE_PARTIAL:
        return "add real-message samples for missing families: " + ", ".join(missing)
    if readiness == READINESS_SAMPLE_MISSING:
        return "add real-message samples before behavior changes"
    return "confirm whether the module is prompt-claim/monitor-only or add reply families"


def summarize_module_readiness(samples=None, strict_modules=()):
    strict = {str(name or "").strip() for name in tuple(strict_modules or ()) if str(name or "").strip()}
    _, sample_families = _sample_modules_and_families(samples) if samples is not None else (set(), set())
    rows = []
    totals = {
        "modules": 0,
        "active_modules": 0,
        "archived_modules": 0,
        "reply_family_modules": 0,
        "sample_complete_modules": 0,
        "sample_partial_modules": 0,
        "sample_missing_modules": 0,
        "contract_only_modules": 0,
        "archived_reply_families": 0,
        "archived_covered_sample_families": 0,
        "covered_sample_families": 0,
        "missing_sample_families": 0,
        "reply_families": 0,
        "strict_modules": 0,
    }

    for manifest in _MANIFESTS:
        reply_families = tuple(manifest.reply_families or ())
        covered = [family for family in reply_families if family in sample_families]
        if manifest.archived:
            missing = []
            readiness = READINESS_ARCHIVED
        else:
            missing = [family for family in reply_families if family not in sample_families]
            readiness = _module_readiness(reply_families, covered, missing)
        strict_match = manifest.name in strict

        totals["modules"] += 1
        if manifest.archived:
            totals["archived_modules"] += 1
            totals["archived_reply_families"] += len(reply_families)
            totals["archived_covered_sample_families"] += len(covered)
        else:
            totals["active_modules"] += 1
            totals["reply_families"] += len(reply_families)
            totals["covered_sample_families"] += len(covered)
            totals["missing_sample_families"] += len(missing)
            if reply_families:
                totals["reply_family_modules"] += 1
        if strict_match:
            totals["strict_modules"] += 1
        if readiness == READINESS_SAMPLE_COMPLETE:
            totals["sample_complete_modules"] += 1
        elif readiness == READINESS_SAMPLE_PARTIAL:
            totals["sample_partial_modules"] += 1
        elif readiness == READINESS_SAMPLE_MISSING:
            totals["sample_missing_modules"] += 1
        elif readiness == READINESS_ARCHIVED:
            pass
        else:
            totals["contract_only_modules"] += 1

        rows.append(
            {
                "module": manifest.name,
                "readiness": readiness,
                "reply_family_count": len(reply_families),
                "covered_sample_count": len(covered),
                "missing_sample_count": len(missing),
                "reply_families": list(reply_families),
                "covered_sample_families": covered,
                "missing_sample_families": missing,
                "send_policy": manifest.send_policy,
                "active_query_policy": manifest.active_query_policy,
                "duplicate_guard": manifest.duplicate_guard,
                "archived": bool(manifest.archived),
                "archive_reason": manifest.archive_reason,
                "strict": strict_match,
                "next_action": _module_next_action(readiness, missing),
            }
        )

    return {
        "modules": rows,
        "totals": totals,
        "unknown_strict_modules": sorted(strict - {manifest.name for manifest in _MANIFESTS}),
    }


def summarize_module_contracts(samples=None, strict_modules=()):
    """Return a read-only module contract matrix for audits and reports."""
    strict = {str(name or "").strip() for name in tuple(strict_modules or ()) if str(name or "").strip()}
    sample_modules, sample_families = _sample_modules_and_families(samples) if samples is not None else (set(), set())
    report_only_summary = summarize_report_only_feature_contracts(strict_modules)
    rust_alignment_summary = summarize_rust_alignment_candidates(
        strict_modules,
        known_external={manifest.name for manifest in _MANIFESTS} | _report_only_contract_names_and_keys(),
    )
    readiness_summary = summarize_module_readiness(samples, strict_modules)
    rows = []
    total_families = 0
    covered_families = 0
    passive_first = 0
    last_resort = 0
    observation_routes = 0

    for manifest in _MANIFESTS:
        replay_modules = tuple(manifest.replay_modules or ())
        reply_families = tuple(manifest.reply_families or ())
        workflow_names = tuple(manifest.workflow_names or ())
        covered = [family for family in reply_families if family in sample_families]
        missing = [] if manifest.archived else [family for family in reply_families if family not in sample_families]
        has_any_sample = any(module in sample_modules for module in replay_modules) or bool(covered)
        has_observation_route = _has_observation_route(manifest)
        readiness = READINESS_ARCHIVED if manifest.archived else _module_readiness(reply_families, covered, missing)

        if not manifest.archived:
            total_families += len(reply_families)
            covered_families += len(covered)
            if manifest.send_policy == SEND_POLICY_PASSIVE_FIRST:
                passive_first += 1
            if manifest.active_query_policy == ACTIVE_QUERY_LAST_RESORT:
                last_resort += 1
            if has_observation_route:
                observation_routes += 1

        rows.append(
            {
                "module": manifest.name,
                "state_key": manifest.state_key,
                "replay_modules": list(replay_modules),
                "reply_families": list(reply_families),
                "workflow_names": list(workflow_names),
                "send_policy": manifest.send_policy,
                "active_query_policy": manifest.active_query_policy,
                "duplicate_guard": manifest.duplicate_guard,
                "replay_required": bool(manifest.replay_required),
                "phaseful": manifest.duplicate_guard == "phaseful",
                "archived": bool(manifest.archived),
                "archive_reason": manifest.archive_reason,
                "observation_route": has_observation_route,
                "has_any_sample": has_any_sample,
                "covered_sample_families": covered,
                "missing_sample_families": missing,
                "readiness": readiness,
                "next_action": _module_next_action(readiness, missing),
                "strict": manifest.name in strict,
            }
        )

    return {
        "modules": rows,
        "report_only": report_only_summary,
        "rust_alignment": rust_alignment_summary,
        "readiness": readiness_summary,
        "totals": {
            "modules": len(rows),
            "active_modules": len(tuple(iter_module_manifests(include_archived=False))),
            "archived_modules": len(tuple(iter_archived_module_manifests())),
            "reply_families": total_families,
            "covered_sample_families": covered_families,
            "passive_first_modules": passive_first,
            "last_resort_modules": last_resort,
            "observation_route_modules": observation_routes,
            "strict_modules": len(strict & {manifest.name for manifest in _MANIFESTS}),
            "strict_report_only_modules": report_only_summary["totals"]["strict_modules"],
            "strict_rust_alignment_candidates": rust_alignment_summary["totals"]["strict_candidates"],
        },
        "unknown_strict_modules": sorted(
            strict
            - {manifest.name for manifest in _MANIFESTS}
            - _report_only_contract_names_and_keys()
            - _rust_alignment_candidate_names_and_keys()
        ),
    }


def validate_module_admission_contract(samples=None, strict_modules=()):
    """Validate the lightweight contract for adding or tightening automation modules.

    This is intentionally weaker than a runtime registry migration. It blocks new
    unobservable or untraceable modules without forcing every legacy module to grow
    full replay coverage in one change.
    """
    strict = {str(name or "").strip() for name in tuple(strict_modules or ()) if str(name or "").strip()}
    sample_modules, sample_families = _sample_modules_and_families(samples) if samples is not None else (set(), set())

    missing_duplicate_guard = []
    last_resort_without_passive_first = []
    passive_without_observation = []
    strict_unknown_modules = []
    strict_archived_modules = []
    strict_missing_replay_routes = []
    strict_missing_samples = []
    strict_missing_sample_families = []

    known_modules = {manifest.name for manifest in _MANIFESTS}
    known_contracts = known_modules | _report_only_contract_names_and_keys() | _rust_alignment_candidate_names_and_keys()
    for name in sorted(strict - known_contracts):
        strict_unknown_modules.append(name)
    report_only_validation = validate_report_only_feature_contracts()
    rust_alignment_validation = validate_rust_alignment_candidates()

    for manifest in _MANIFESTS:
        if manifest.archived:
            if manifest.name in strict:
                strict_archived_modules.append(manifest.name)
            continue
        if not str(manifest.duplicate_guard or "").strip():
            missing_duplicate_guard.append(manifest.name)
        if (
            manifest.active_query_policy == ACTIVE_QUERY_LAST_RESORT
            and manifest.send_policy != SEND_POLICY_PASSIVE_FIRST
        ):
            last_resort_without_passive_first.append(manifest.name)
        has_observation_route = _has_observation_route(manifest)
        if manifest.send_policy == SEND_POLICY_PASSIVE_FIRST and not has_observation_route:
            passive_without_observation.append(manifest.name)

        if manifest.name not in strict:
            continue
        has_replay_route = bool(tuple(manifest.replay_modules or ()))
        has_reply_route = bool(tuple(manifest.reply_families or ()))
        if not has_replay_route or not has_reply_route:
            strict_missing_replay_routes.append(manifest.name)
        if samples is not None:
            has_sample = any(module in sample_modules for module in tuple(manifest.replay_modules or ())) or any(
                family in sample_families for family in tuple(manifest.reply_families or ())
            )
            if not has_sample:
                strict_missing_samples.append(manifest.name)
            for family in tuple(manifest.reply_families or ()):
                if family not in sample_families:
                    strict_missing_sample_families.append(f"{manifest.name}:{family}")

    replay_validation = validate_replay_sample_coverage(samples) if samples is not None else None
    replay_ok = True if replay_validation is None else replay_validation["ok"]
    return {
        "ok": (
            replay_ok
            and report_only_validation["ok"]
            and rust_alignment_validation["ok"]
            and not missing_duplicate_guard
            and not last_resort_without_passive_first
            and not passive_without_observation
            and not strict_unknown_modules
            and not strict_missing_replay_routes
            and not strict_missing_samples
        ),
        "replay_validation": replay_validation,
        "missing_duplicate_guard": missing_duplicate_guard,
        "last_resort_without_passive_first": last_resort_without_passive_first,
        "passive_without_observation": passive_without_observation,
        "strict_unknown_modules": strict_unknown_modules,
        "strict_archived_modules": strict_archived_modules,
        "strict_missing_replay_routes": strict_missing_replay_routes,
        "strict_missing_samples": strict_missing_samples,
        "strict_missing_sample_families": strict_missing_sample_families,
        "strict_report_only_modules": sorted(strict & _report_only_contract_names_and_keys()),
        "strict_rust_alignment_candidates": sorted(strict & _rust_alignment_candidate_names_and_keys()),
        "report_only_validation": report_only_validation,
        "rust_alignment_validation": rust_alignment_validation,
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
        if manifest.archived:
            continue
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
        if manifest.archived:
            missing = []
        else:
            missing = [family for family in families if family not in sample_families]
            family_total += len(families)
            covered_total += len(covered)
            if missing:
                all_missing.extend({"module": manifest.name, "family": family} for family in missing)
        modules.append(
            {
                "module": manifest.name,
                "archived": bool(manifest.archived),
                "archive_reason": manifest.archive_reason,
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
        "active_modules": len(tuple(iter_module_manifests(include_archived=False))),
        "archived_modules": len(tuple(iter_archived_module_manifests())),
        "total_families": family_total,
        "covered_families": covered_total,
        "missing_families": all_missing,
        "modules": modules,
    }
