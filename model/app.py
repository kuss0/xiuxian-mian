import asyncio
import signal
import time
import traceback
from types import SimpleNamespace

from telethon import events

from .app_message_log import (
    _append_game_group_message_log,
    _append_replica_dispatch_group_message_log,
    _append_replica_group_message_log,
)
from .app_runtime import (
    _claim_runtime_event,
    _get_event_reply_header_msg_id,
    _has_runtime_message_consumed,
    _mark_runtime_message_consumed,
)
from .app_replica import (
    handle_replica_button_callback,
    _handle_replica_dispatch_group_command,
    _handle_replica_group_command,
    _handle_replica_join_reply,
    _handle_replica_progress_event,
    _handle_virtual_hall_auto_game_event,
    _cleanup_replica_run_state,
    handle_huanglong_conscription_text,
    _mark_replica_team_joined_from_text,
    is_replica_group_command_text,
    run_huanglong_conscription_scheduler,
    run_luoyun_cd_reminder_scheduler,
)
from .config import BOT_SILENCE_TIMEOUT_SEC, CMD_IDENTITY_INFO, client, create_account_client, get_all_clients, get_registered_client, is_account_offline, mark_account_offline, register_client
from .control import enforce_identity_module_availability, handle_identity_info_reply, handle_log_group_command, handle_passive_identity_profile_card, handle_realm_breakthrough_broadcast, hydrate_identity_profile, initialize_identity_runtime, register_message_box_shadow_payload_provider, run_identity_info_followup_scheduler, run_startup_account_integrity_check, scan_startup_timeout_tasks, spread_overdue_runtime_timers, toggle_global_enabled
from .module_manifest import is_module_archived
from .features.checkin import handle_checkin_reply, handle_sect_teach_reply, run_checkin_scheduler
from .features._phaseful import has_phaseful_summary_block, observe_phaseful_identity_message
from .features.deep_retreat import (
    handle_deep_retreat_running_reply,
    handle_deep_retreat_status_reply,
    handle_deep_retreat_success_reply,
    handle_deep_retreat_summary_broadcast,
    run_deep_retreat_scheduler,
)
from .features.divination import handle_divination_exchange_reply, handle_divination_reply, run_divination_scheduler
from .features.dungeon_quiet import clear_expired_dungeon_quiet, observe_dungeon_quiet_text
from .features.guanxing import (
    handle_guanxing_external_shift_command,
    handle_guanxing_finish_broadcast,
    handle_guanxing_query_reply,
    restore_guanxing_round_runtime,
    run_guanxing_scheduler,
)
from .features.formation import handle_formation_event, is_formation_reply_text, run_formation_scheduler
from .features.guanxing_monitor import handle_guanxing_monitor_broadcast, restore_guanxing_monitor_runtime_state, run_guanxing_monitor_scheduler
from .features.hehuan import run_hehuan_scheduler
from .features.concubine import (
    handle_concubine_affinity_event,
    handle_concubine_dream_reply,
    handle_concubine_fragment_reply,
    handle_concubine_gift_reply,
    handle_concubine_greet_reply,
    handle_concubine_loss_broadcast,
    handle_concubine_puzzle_reply,
    handle_concubine_reacquire_reply,
    handle_concubine_status_reply,
    handle_concubine_storage_bag_reply,
    handle_concubine_heart_reply,
    handle_concubine_tianji_reply,
    is_concubine_affinity_event_candidate,
    restore_concubine_runtime,
    run_concubine_phaseful_cleanup_scheduler,
    run_concubine_scheduler,
)
from .features.pet import handle_pet_cd_fix, handle_pet_warm_reply, handle_pet_trial_reply, run_pet_scheduler
from .features.passive_inbox import handle_passive_module_card, record_passive_inbox_event
from .features.ranch import handle_ranch_reply, handle_ranch_return_broadcast, run_ranch_scheduler
from .features.rare_daily_report import run_rare_daily_report_scheduler
from .features.jiyin import handle_jiyin_delayed_action_result, handle_jiyin_prompt, run_jiyin_scheduler
from .features.join_dungeon import handle_dungeon_join_bot_message, handle_dungeon_join_mention, record_game_group_message
from .features.nanlong import handle_nanlong_prompt, handle_nanlong_reply, handle_nanlong_result_broadcast, run_nanlong_scheduler
from .features.quiz import handle_quiz_learning_prompt, handle_quiz_prompt, handle_quiz_result_broadcast, run_quiz_learning_scheduler, run_quiz_scheduler
from .features.tianti import handle_tianti_reply, run_tianti_scheduler
from .features.tiandao_judgement import handle_tiandao_judgement_prompt, handle_tiandao_judgement_punishment, run_tiandao_judgement_scheduler
from .features.tianji_quiz import handle_tianji_quiz_prompt, handle_tianji_quiz_result_broadcast, run_tianji_quiz_scheduler
from .features.yinluo import run_yinluo_scheduler
from .features.world_boss import handle_world_boss_broadcast, handle_world_boss_reply, run_world_boss_scheduler
from .features.small_world import (
    handle_small_world_barrier_reply,
    handle_small_world_disaster_broadcast,
    handle_small_world_harvest_reply,
    handle_small_world_manifest_reply,
    handle_small_world_preach_reply,
    handle_small_world_query_reply,
    handle_small_world_refine_reply,
    run_small_world_scheduler,
)
from .features.stargazer import (
    handle_stargazer_collect_reply,
    handle_stargazer_guide_reply,
    handle_stargazer_panel,
    handle_stargazer_soothe_reply,
    handle_stargazer_sync_reply,
    run_stargazer_scheduler,
)
from .features.storage_bag import handle_storage_bag_reply, handle_storage_bag_transfer_reply, is_storage_transfer_waiting_reply, run_storage_bag_transfer_scheduler
from .features.tower import handle_tower_reply, run_tower_scheduler
from .features.explore_rift import handle_explore_rift_reply, run_explore_rift_scheduler
from .features.tree import (
    handle_tree_cd_fix,
    handle_tree_exception_prompt,
    handle_tree_harvest_reply,
    handle_tree_invasion_end,
    handle_tree_invasion_start,
    handle_tree_panel,
    handle_tree_rebirth_reset,
    run_tree_bootstrap_check,
    run_tree_scheduler,
)
from .features.second_soul import (
    handle_second_soul_choice_result_broadcast,
    handle_second_soul_demon_status_reply,
    handle_second_soul_heart_demon_warning_broadcast,
    handle_second_soul_purge_reply,
    handle_second_soul_recovery_broadcast,
    handle_second_soul_return_broadcast,
    handle_second_soul_status_reply,
    handle_second_soul_train_reply,
    run_second_soul_bootstrap_check,
    run_second_soul_scheduler,
)
from .features.taiyi import (
    handle_taiyi_node_define_reply,
    handle_taiyi_node_search_reply,
    handle_taiyi_yindao_reply,
    run_taiyi_bootstrap_check,
    run_taiyi_scheduler,
)
from .features.yuanying import (
    handle_yuanying_running_reply,
    handle_yuanying_status_reply,
    handle_yuanying_success_reply,
    handle_yuanying_summary_broadcast,
    run_yuanying_scheduler,
)
from .features.wendao import handle_wendao_reply, run_wendao_scheduler
from .features.duel import handle_duel_broadcast, handle_duel_reply, run_duel_scheduler
from .features.fishing_runtime import handle_fishing_reply, is_fishing_reply_text, run_fishing_scheduler
from .features.wild_training import handle_wild_training_reply, run_wild_training_scheduler
from .persistence import (
    flush_if_dirty,
    get_persistence_write_failure,
    has_persisted_identity_rows,
    has_persistence_write_failure,
    load_state,
    mark_dirty,
    save_state,
)
from .action_guard import close_by_family as close_action_guard_by_family
from .delayed_actions import drain_due_actions
from .message_contract import record_unhandled_routed_reply
from .message_box import (
    MessageBox,
    build_message_box_snapshot_payload,
    build_message_fact_from_event,
    write_message_box_snapshot_payload,
)
from .verified_event import from_telegram_event, is_new_delivery
from .runtime import (
    _fire_and_forget,
    check_bot_health_timeout,
    clear_all_pending_tasks,
    clear_pending_by_reply,
    console_log,
    gc_my_msg_ids,
    gc_ui_login_tokens,
    gc_ui_sessions,
    get_reply_context,
    is_identity_weak,
    is_account_session_error,
    is_reply_to_identity_message,
    mark_bot_health_recovered,
    note_game_bot_message,
    note_game_command_observed,
    note_identity_weakness,
    resolve_reply_family,
    run_retry_scheduler,
    schedule_cleanup,
    send_game_command,
    should_pause_for_bot_health,
    track_reply_chain_message,
    mono,
    run_log_bot_callback_poller,
    send_audit_log,
)
from .state import (
    IDENTITY_TIMER_COLUMNS,
    get_accounts,
    get_game_bot_ids,
    get_game_group_id,
    get_global_enabled,
    get_identity_account,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    get_send_as_profile,
    set_game_bot_ids,
    state,
    use_identity,
)
from .timing import fmt_time_after
from .ui import run_storage_bag_api_keepalive_scheduler, start_ui_server, stop_ui_server

_bot_silence_auto_paused = False
_identity_scheduler_task = None
_identity_scheduler_started_at = 0.0
_identity_scheduler_last_warn_at = 0.0
_log_bot_callback_task = None
_phaseful_scheduler_task = None
_small_world_scheduler_task = None
_suspected_game_bot_hits = {}
_MESSAGE_BOX_SHADOW_CAP = 10000
_message_box_shadow = MessageBox(cap=_MESSAGE_BOX_SHADOW_CAP)

IDENTITY_SCHEDULER_STUCK_WARN_SEC = 15 * 60
UNKNOWN_GAME_BOT_LEARN_THRESHOLD = 3
UNKNOWN_GAME_BOT_HIT_TTL_SEC = 24 * 3600
HAN_TIANZUN_BOT_NAME = "韩天尊"
TIANZUN_BOT_NAME_MARKER = "天尊"

_PHASEFUL_IDENTITY_SCHEDULERS = (
    run_deep_retreat_scheduler,
    run_yuanying_scheduler,
)
_ORDINARY_IDENTITY_SCHEDULERS = (
    run_pet_scheduler,
    run_ranch_scheduler,
    run_wild_training_scheduler,
    run_stargazer_scheduler,
    run_formation_scheduler,
    run_tianti_scheduler,
    run_quiz_scheduler,
    run_jiyin_scheduler,
    run_concubine_scheduler,
    run_hehuan_scheduler,
    run_nanlong_scheduler,
    run_yinluo_scheduler,
    run_small_world_scheduler,
    run_explore_rift_scheduler,
    run_wendao_scheduler,
    run_duel_scheduler,
    run_fishing_scheduler,
    run_tree_bootstrap_check,
    run_tree_scheduler,
    run_checkin_scheduler,
    run_tower_scheduler,
    run_second_soul_bootstrap_check,
    run_second_soul_scheduler,
    run_taiyi_bootstrap_check,
    run_taiyi_scheduler,
)
_PHASEFUL_BLOCK_CLEANUP_SCHEDULERS = (
    run_concubine_phaseful_cleanup_scheduler,
)
_GLOBAL_SCHEDULERS = (
    ("delayed_actions", drain_due_actions),
    ("guanxing_monitor", run_guanxing_monitor_scheduler),
    ("guanxing", run_guanxing_scheduler),
    ("storage_bag_api_keepalive", run_storage_bag_api_keepalive_scheduler),
    ("storage_bag_transfer", run_storage_bag_transfer_scheduler),
    ("divination", run_divination_scheduler),
    ("world_boss", run_world_boss_scheduler),
    ("tiandao_judgement", run_tiandao_judgement_scheduler),
    ("tianji_quiz", run_tianji_quiz_scheduler),
    ("huanglong_conscription", run_huanglong_conscription_scheduler),
    ("luoyun_cd_reminder", run_luoyun_cd_reminder_scheduler),
)

_SCHEDULER_MANIFEST_BRIDGE = {
    "delayed_actions": {"manifest_names": (), "helper": True},
    "guanxing_monitor": {"manifest_names": ("观星监控",), "helper": False},
    "guanxing": {"manifest_names": ("观星",), "helper": False},
    "storage_bag_api_keepalive": {"manifest_names": ("储物袋",), "helper": True},
    "storage_bag_transfer": {"manifest_names": ("储物袋",), "helper": False},
    "divination": {"manifest_names": ("卜筮问天",), "helper": False},
    "world_boss": {"manifest_names": ("真仙试锋",), "helper": False},
    "tiandao_judgement": {"manifest_names": (), "helper": True},
    "tianji_quiz": {"manifest_names": (), "helper": True},
    "huanglong_conscription": {"manifest_names": ("自动副本",), "helper": True},
    "luoyun_cd_reminder": {"manifest_names": ("自动副本",), "helper": True},
    "run_checkin_scheduler": {"manifest_names": ("点卯", "宗门传功"), "helper": False},
    "run_concubine_scheduler": {"manifest_names": ("侍妾", "天机代卜", "共历心劫", "侍妾远航"), "helper": False},
    "run_deep_retreat_scheduler": {"manifest_names": ("深度闭关",), "helper": False},
    "run_formation_scheduler": {"manifest_names": ("周天星斗",), "helper": False},
    "run_hehuan_scheduler": {"manifest_names": ("合欢宗",), "helper": False},
    "run_jiyin_scheduler": {"manifest_names": ("极阴祖师",), "helper": False},
    "run_nanlong_scheduler": {"manifest_names": ("南陇侯",), "helper": False},
    "run_pet_scheduler": {"manifest_names": ("法宝", "温养器灵", "器灵试炼"), "helper": False},
    "run_quiz_scheduler": {"manifest_names": ("玄骨考校",), "helper": False},
    "run_ranch_scheduler": {"manifest_names": ("放养",), "helper": False},
    "run_second_soul_bootstrap_check": {"manifest_names": ("第二元神",), "helper": True},
    "run_second_soul_scheduler": {"manifest_names": ("第二元神",), "helper": False},
    "run_small_world_scheduler": {"manifest_names": ("小世界",), "helper": False},
    "run_explore_rift_scheduler": {"manifest_names": ("探寻裂缝",), "helper": False},
    "run_stargazer_scheduler": {"manifest_names": ("观星台",), "helper": False},
    "run_taiyi_bootstrap_check": {"manifest_names": ("太一",), "helper": True},
    "run_taiyi_scheduler": {"manifest_names": ("太一",), "helper": False},
    "run_tianti_scheduler": {"manifest_names": ("登天阶",), "helper": False},
    "run_tower_scheduler": {"manifest_names": ("闯塔",), "helper": False},
    "run_tree_bootstrap_check": {"manifest_names": ("灵树",), "helper": True},
    "run_tree_scheduler": {"manifest_names": ("灵树",), "helper": False},
    "run_wendao_scheduler": {"manifest_names": ("问道",), "helper": False},
    "run_duel_scheduler": {"manifest_names": ("斗法",), "helper": False},
    "run_fishing_scheduler": {"manifest_names": ("灵溪垂钓",), "helper": False},
    "run_wild_training_scheduler": {"manifest_names": ("野外历练",), "helper": False},
    "run_yinluo_scheduler": {"manifest_names": ("阴罗宗",), "helper": False},
    "run_yuanying_scheduler": {"manifest_names": ("元婴",), "helper": False},
}


def _scheduler_function_names(schedulers):
    return tuple(scheduler.__name__ for scheduler in schedulers)


def _is_tree_runtime_archived():
    return is_module_archived("灵树")


def _get_message_box_shadow_snapshot():
    return _message_box_shadow.snapshot()


def get_message_box_shadow_payload(*, include_edits=True, limit=None, now=None):
    return build_message_box_snapshot_payload(
        _message_box_shadow.snapshot(),
        include_edits=include_edits,
        limit=limit,
        now=now,
    )


def write_message_box_shadow_snapshot(path, *, include_edits=True, limit=None, now=None):
    payload = get_message_box_shadow_payload(include_edits=include_edits, limit=limit, now=now)
    return write_message_box_snapshot_payload(path, payload)


register_message_box_shadow_payload_provider(get_message_box_shadow_payload)


def _reset_message_box_shadow_for_test():
    global _message_box_shadow
    _message_box_shadow = MessageBox(cap=_MESSAGE_BOX_SHADOW_CAP)


def _record_message_box_shadow(
    event,
    text,
    reply_context=None,
    *,
    reply_to=None,
    event_type="message",
    is_game_bot=False,
    is_game_group=None,
):
    try:
        if is_game_group is None:
            is_game_group = int(getattr(event, "chat_id", 0) or 0) == int(get_game_group_id() or 0)
        fact = build_message_fact_from_event(
            event,
            text,
            reply_context,
            reply_to=reply_to,
            event_type=event_type,
            is_game_group=bool(is_game_group),
            is_game_bot=bool(is_game_bot),
            source="telegram_shadow",
        )
        _message_box_shadow.upsert(fact)
        return fact
    except Exception:
        return None


def get_identity_scheduler_order_contract():
    return {
        "phaseful": _scheduler_function_names(_PHASEFUL_IDENTITY_SCHEDULERS),
        "ordinary": _scheduler_function_names(_ORDINARY_IDENTITY_SCHEDULERS),
    }


def get_global_scheduler_order_contract():
    return tuple(name for name, _scheduler in _GLOBAL_SCHEDULERS)


def get_scheduler_manifest_bridge_contract():
    return {
        name: {
            "manifest_names": tuple(entry.get("manifest_names") or ()),
            "helper": bool(entry.get("helper")),
        }
        for name, entry in _SCHEDULER_MANIFEST_BRIDGE.items()
    }

BOT_REPLY_FAMILY_HINTS = {
    "checkin": ("点卯", "已点卯", "已经点过", "宗门"),
    "sect_teach": ("传功", "宗门", "贡献"),
    "tower": ("闯塔", "古塔", "塔灵", "挑战", "道心受挫"),
    "pet": ("器灵", "法宝", "默契", "经验", "休息"),
    "pet_warm": ("温养器灵", "温养", "灵光大振", "吞纳过灵机"),
    "pet_trial": ("器灵试炼", "试炼", "共鸣", "灵潮", "反噬"),
    "tree_panel": ("灵眼之树", "灵树", "果实", "采摘", "成熟", "定脉", "脉象"),
    "tree_pulse": ("定脉", "注灵", "固脉", "净浊", "冲脉", "脉稳", "浊息"),
    "tree_guard": ("守山", "护山", "攻山", "灵树"),
    "tree_harvest": ("采摘", "灵果", "木髓", "灵树"),
    "wild_training": ("野外历练", "荒野深处", "山中灵机未复", "妖兽遭遇", "负伤而归", "灵机暗藏"),
    "stargazer_panel": ("观星台", "引星盘", "星辰"),
    "stargazer_guide": ("牵引", "星辰", "引星盘", "星力"),
    "stargazer_soothe": ("安抚", "狂暴星力", "引星盘"),
    "stargazer_collect": ("收集", "精华", "星辰", "引星盘"),
    "stargazer_sync": ("观星台", "引星盘", "星辰"),
    "guanxing_query": ("观星台", "引星盘", "空闲", "精华"),
    "guanxing_shift": ("牵引", "星辰", "引星盘", "星力"),
    "formation_start": ("周天星斗", "大阵", "启阵", "助阵", "星宫"),
    "formation_assist": ("周天星斗", "大阵", "助阵", "成阵", "心神消耗巨大"),
    "tianti_status": ("天梯", "问心", "罡风", "登天"),
    "tianti_wenxin": ("问心", "天梯", "道心"),
    "tianti_climb": ("天梯", "登天", "层", "修为"),
    "tianti_gangfeng": ("九天罡风", "罡风", "再聚"),
    "yuanying": ("元婴", "出窍", "归窍", "法则碎片", "探寻"),
    "wendao": ("问道", "问道得宝", "宗门长老", "天机不可频繁窥探"),
    "duel": ("斗法", "天道战报", "斗法终局", "正在锁定对手天机", "法宝齐出", "战斗结束，正在整理天道战报"),
    "fishing": ("灵溪垂钓", "提竿成功", "空竿", "剖鱼取机缘", "渔具铺", "打窝已成", "鱼篓"),
    "deep_retreat": ("深度闭关", "闭关", "神魂", "功成圆满", "总结"),
    "small_world_preach": ("小世界", "香火", "信仰", "神识", "神迹"),
    "small_world_relief": ("小世界", "赈灾", "甘霖", "神谕", "稳定", "人口"),
    "small_world_query": ("小世界", "香火", "祈愿", "显灵", "紫府"),
    "small_world_manifest": ("显灵", "祈愿", "清灵丹", "灵石", "小世界"),
    "small_world_harvest": ("收割香火", "香火", "库存", "小世界"),
    "small_world_refine": ("神识淬炼", "香火", "神识", "小世界"),
    "small_world_barrier": ("护界禁制", "愿力金幕", "随机天灾", "香火"),
    "explore_rift": ("探寻成功", "激战得胜", "遭遇风暴", "不敌败退", "大凶·虚空噬体", "元婴遁逃·虚弱", "夺舍重生", "三具可供夺舍", "夺舍成功", "天道代择", "探寻裂缝", "满载而归", "法则碎片", "空间裂缝", "时空异兽"),
    "concubine_status": ("侍妾", "道侣", "红尘", "情缘", "残图"),
    "concubine_greet": ("侍妾", "情缘", "问安", "心意"),
    "concubine_gift": ("侍妾", "情缘", "赠予", "灵石"),
    "concubine_dream": ("入梦寻图", "侍妾", "残图", "梦图感应"),
    "concubine_fragment": ("虚天残图", "残图", "残纹", "拼片"),
    "concubine_puzzle": ("拼图", "虚天残图", "拼合", "残纹"),
    "concubine_reacquire": ("侍妾", "道侣", "红尘寻缘", "宗门赐婚", "红颜"),
    "concubine_tianji": ("天机代卜", "天机链路", "卜算天机", "代卜"),
    "concubine_heart": ("共历心劫", "坠魔心劫", "心劫余波", "心劫抉择"),
    "hehuan_retreat": ("闭关双修", "合欢宗", "闭关成功", "灵脉加持"),
    "hehuan_contract": ("缔结同参", "同参契印", "双修", "契印"),
    "hehuan_dual": ("双修", "温养双修", "契印感应", "采补", "心神尚未恢复"),
    "hehuan_seal": ("种下心印", "心印", "炉鼎", "心神之战"),
    "hehuan_escape": ("挣脱心印", "心印", "炉鼎", "挣脱"),
    "nanlong": ("南陇侯", "交易", "侍妾", "法宝", "功法"),
    "second_soul_status": ("第二元神", "元神", "心魔", "修炼"),
    "second_soul_train": ("第二元神", "元神", "修炼", "闭关"),
    "second_soul_choice": ("心魔", "抉择", "第二元神"),
    "second_soul_purge": ("元神镇魔", "镇魔", "魔染"),
    "second_soul_demon_status": ("五子同心魔", "魔染", "同心"),
    "taiyi_yindao": ("引道", "太一", "五行", "神识"),
    "taiyi_node_search": ("搜寻节点", "空间节点", "虚空", "神识"),
    "taiyi_node_define": ("定星", "空间节点", "稳固", "材料"),
    "world_boss": ("真仙试锋", "讨伐青元子", "青元子", "魔压", "阵势", "世界Boss"),
}


def _track_manual_game_command(sender_id, text, msg_id):
    command = str(text or "").strip()
    if not command:
        return
    family = resolve_reply_family(command)
    if family:
        track_reply_chain_message(
            msg_id,
            sender_id,
            family,
            root_msg_id=msg_id,
            source="manual_game_command",
        )


def _resolve_identity_sender_id(sender_id):
    try:
        sender_id = int(sender_id or 0)
    except (TypeError, ValueError):
        return 0
    if sender_id == 0:
        return 0

    candidates = [sender_id]
    if sender_id < 0:
        sender_abs = str(abs(sender_id))
        if sender_abs.startswith("100") and len(sender_abs) > 3:
            try:
                candidates.append(int(sender_abs[3:]))
            except ValueError:
                pass

    identity_ids = {int(identity_id) for identity_id in get_identity_ids()}
    for candidate in candidates:
        if int(candidate or 0) in identity_ids:
            return int(candidate)
    return 0


def _looks_like_game_bot_reply(text, family):
    raw_text = str(text or "").strip()
    if not raw_text or raw_text.startswith("."):
        return False
    hints = BOT_REPLY_FAMILY_HINTS.get(str(family or "").strip()) or ()
    return any(hint in raw_text for hint in hints)


def _normalize_sender_display_name(text):
    return "".join(str(text or "").split())


def _entity_is_han_tianzun_bot(entity):
    if entity is None or not bool(getattr(entity, "bot", False)):
        return False
    candidates = []
    first_name = str(getattr(entity, "first_name", "") or "")
    last_name = str(getattr(entity, "last_name", "") or "")
    title = str(getattr(entity, "title", "") or "")
    if first_name or last_name:
        candidates.append(f"{first_name}{last_name}")
        candidates.append(f"{first_name} {last_name}")
    candidates.extend([first_name, title])
    for name in candidates:
        normalized_name = _normalize_sender_display_name(name)
        if normalized_name == HAN_TIANZUN_BOT_NAME or TIANZUN_BOT_NAME_MARKER in normalized_name:
            return True
    return False


async def _learn_game_bot_id(sender_id, reason):
    sender_id = int(sender_id or 0)
    if sender_id <= 0 or sender_id in set(get_game_bot_ids()):
        return False
    known_ids = set(get_game_bot_ids())
    known_ids.add(sender_id)
    set_game_bot_ids(sorted(known_ids))
    save_state()
    await send_audit_log(
        f"🧩 识别到游戏 bot：{sender_id}｜{reason}，已加入 game_bot_ids。",
        scope="global",
        limit=220,
    )
    return True


async def _is_game_bot_event(event):
    sender_id = int(getattr(event, "sender_id", 0) or 0)
    if sender_id in set(get_game_bot_ids()):
        setattr(event, "_xiuxian_sender_is_game_bot", True)
        return True
    sender = getattr(event, "sender", None)
    if sender is None:
        try:
            sender = await event.get_sender()
        except Exception:
            sender = None
    if _entity_is_han_tianzun_bot(sender):
        setattr(event, "_xiuxian_sender_is_game_bot", True)
        await _learn_game_bot_id(sender_id, "bot 名称=韩天尊")
        return True
    setattr(event, "_xiuxian_sender_is_game_bot", False)
    return False


async def _note_game_bot_activity():
    global _bot_silence_auto_paused
    bot_health_action = note_game_bot_message(time.time())
    if bot_health_action == "probe":
        if _bot_silence_auto_paused:
            _fire_and_forget(_send_bot_health_probe())
    elif bot_health_action == "recover":
        if _bot_silence_auto_paused and not get_global_enabled():
            await toggle_global_enabled(True, source="bot_health_recovery")
        _bot_silence_auto_paused = False
        mark_bot_health_recovered("bot 恢复确认完成")


async def _record_suspected_game_bot(sender_id, family, text):
    sender_id = int(sender_id or 0)
    if sender_id == 0 or sender_id in set(get_game_bot_ids()):
        return
    now = time.time()
    item = _suspected_game_bot_hits.get(sender_id) or {"count": 0, "first_seen": now, "notified": False, "learned": False}
    if now - float(item.get("first_seen", now) or now) > UNKNOWN_GAME_BOT_HIT_TTL_SEC:
        item = {"count": 0, "first_seen": now, "notified": False, "learned": False}
    item["count"] = int(item.get("count", 0) or 0) + 1
    _suspected_game_bot_hits[sender_id] = item

    if not item.get("notified"):
        item["notified"] = True
        await send_audit_log(
            f"🧩 检测到未登记游戏 bot 回复，已临时放行：{sender_id}｜{family}｜{str(text or '')[:60]}",
            scope="global",
            limit=260,
        )

    if int(item.get("count", 0) or 0) >= UNKNOWN_GAME_BOT_LEARN_THRESHOLD and not item.get("learned"):
        item["learned"] = True
        await _learn_game_bot_id(sender_id, f"连续命中 {item['count']} 次")


async def _handle_suspected_game_bot_reply(event, text, now, *, edited=False):
    sender_id = int(getattr(event, "sender_id", 0) or 0)
    if _resolve_identity_sender_id(sender_id):
        return False
    reply_to, reply_context = await _resolve_event_reply(event)
    routed_identity_id = int((reply_context or {}).get("send_as_id") or 0)
    matched_family = (reply_context or {}).get("family") or None
    if routed_identity_id <= 0 or not matched_family:
        return False
    if not _looks_like_game_bot_reply(text, matched_family):
        return False
    _record_message_box_shadow(
        event,
        text,
        reply_context,
        reply_to=reply_to,
        event_type="edit" if edited else "message",
        is_game_bot=True,
        is_game_group=True,
    )

    handled_reply = await _handle_routed_reply_event(
        event,
        text,
        now,
        reply_to,
        reply_context,
        event_kind="edit" if edited else "message",
    )
    if handled_reply:
        await _note_game_bot_activity()
        await _record_suspected_game_bot(sender_id, matched_family, text)
    return handled_reply


def _is_identity_account_offline(identity_id):
    account_id = int(get_identity_account(identity_id) or 0)
    return bool(account_id and is_account_offline(account_id))


def _handled_reply_context(reply_context):
    context = dict(reply_context) if isinstance(reply_context, dict) else {}
    context["routed_reply_handled"] = True
    return context


def _is_identity_info_waiting_reply(text):
    raw_text = str(text or "").strip()
    return (
        "正在推演天机" in raw_text
        or "锁定道友神魂" in raw_text
        or "正在为你绘制" in raw_text
    )


def _is_identity_info_reply_observation(text):
    raw_text = str(text or "").strip()
    return _is_identity_info_waiting_reply(raw_text) or "天命玉牒" in raw_text or "战力评估" in raw_text


def _is_manual_storage_trade_observation(matched_family, reply_context):
    family = str(matched_family or "").strip()
    if family not in {"storage_bag_listing", "storage_bag_buy"}:
        return False
    return str((reply_context or {}).get("source") or "").strip() == "manual_game_command"


def _record_manual_storage_trade_observation(event, text, reply_context, *, routed_identity_id=0, event_kind="message"):
    return record_passive_inbox_event(
        "skipped",
        module="储物袋",
        identity_id=int(routed_identity_id or 0),
        reason="manual_storage_trade_observation",
        summary=str((reply_context or {}).get("family") or "storage_bag"),
        family=str((reply_context or {}).get("family") or ""),
        chat_id=int(getattr(event, "chat_id", 0) or 0),
        msg_id=int(getattr(event, "id", 0) or 0),
        reply_to_msg_id=int((reply_context or {}).get("reply_to_msg_id") or 0),
        reply_to_sender_id=int((reply_context or {}).get("reply_to_sender_id") or 0),
        root_msg_id=int((reply_context or {}).get("root_msg_id") or (reply_context or {}).get("reply_to_msg_id") or 0),
        event_type=str(event_kind or "message").strip() or "message",
        route_source=str((reply_context or {}).get("matched_via") or "reply_context"),
        matched_text=text,
        decision="manual_command_observed",
        source_message_id=int(getattr(event, "id", 0) or 0),
        include_recent=False,
    )


def _candidate_fishing_swallowed_reply_identity_ids(text, now):
    if not is_fishing_reply_text(text):
        return []
    raw_lower = str(text or "").lower()
    candidates = []
    username_matched = []
    for identity_id in get_identity_ids():
        identity_id = int(identity_id)
        try:
            identity_state = get_identity_state(identity_id)
        except KeyError:
            continue
        if not identity_state.get("fishing_enabled"):
            continue
        if int(identity_state.get("fishing_reply_to_msg_id", 0) or 0) <= 0:
            continue
        if float(identity_state.get("fishing_reply_due_at", 0) or 0) < float(now):
            continue
        candidates.append(identity_id)
        username = str((get_send_as_profile(identity_id) or {}).get("username") or "").strip().lstrip("@").lower()
        if username and f"@{username}" in raw_lower:
            username_matched.append(identity_id)
    if len(username_matched) == 1:
        return username_matched
    if len(candidates) == 1:
        return candidates
    return []


async def _dispatch_fishing_swallowed_reply_fallback(event, text, now, *, event_kind="message"):
    candidate_ids = _candidate_fishing_swallowed_reply_identity_ids(text, now)
    if not candidate_ids:
        return False
    if not _claim_runtime_event(event, scope=f"fishing_swallowed_reply:{event_kind}:{candidate_ids[0]}"):
        return False
    with use_identity(candidate_ids[0]):
        return await handle_fishing_reply(
            text,
            now,
            reply_to=None,
            matched_family=None,
            result_msg_id=getattr(event, "id", 0),
        )


def _get_bot_health_probe_identity_id():
    for identity_id in get_identity_ids():
        if get_identity_enabled(identity_id) and not _is_identity_account_offline(identity_id):
            return int(identity_id)
    return None


async def _send_bot_health_probe():
    identity_id = _get_bot_health_probe_identity_id()
    if identity_id is None:
        await send_audit_log("🩺 天尊恢复探测跳过：没有可用身份，维持全局暂停。", scope="global", limit=220)
        return
    msg = await send_game_command(CMD_IDENTITY_INFO, track=True, send_as_id=identity_id, priority="probe", max_retry=0)
    if msg:
        await send_audit_log("🩺 天尊恢复探测已发送，等待确认回复后恢复普通调度。", scope="identity", send_as_id=identity_id, limit=220)


def _is_identity_owner_event(event, send_as_id):
    send_as_id = int(send_as_id or 0)
    if send_as_id <= 0:
        return False
    account_id = get_identity_account(send_as_id)
    if not account_id:
        live_clients = [
            tc
            for live_account_id, tc in get_all_clients().items()
            if int(live_account_id or 0) > 0 and not is_account_offline(live_account_id)
        ]
        expected_client = live_clients[0] if len(live_clients) == 1 else client
    elif is_account_offline(account_id):
        return False
    else:
        expected_client = get_registered_client(account_id)
        if expected_client is None:
            return False
    return getattr(event, "client", None) is expected_client


async def _resolve_event_reply(event):
    try:
        reply_to = await event.get_reply_message()
    except Exception as exc:
        if is_account_session_error(exc):
            event_client = getattr(event, "client", None)
            for account_id, tc in get_all_clients().items():
                if event_client is tc:
                    mark_account_offline(account_id, str(exc))
                    break
            reply_to = None
        else:
            raise
    reply_header_msg_id = _get_event_reply_header_msg_id(event)
    reply_context = get_reply_context(reply_to, reply_to_msg_id=reply_header_msg_id)
    if reply_to is not None:
        try:
            reply_context["reply_to_sender_id"] = int(getattr(reply_to, "sender_id", 0) or 0)
        except (TypeError, ValueError):
            reply_context["reply_to_sender_id"] = 0
    if reply_to is None and reply_header_msg_id > 0:
        reply_to = SimpleNamespace(id=reply_header_msg_id, raw_text="")
    return reply_to, reply_context


async def _run_for_all_identities(handler, *args, enabled_only=False):
    for identity_id in get_identity_ids():
        if enabled_only and not get_identity_enabled(identity_id):
            continue
        with use_identity(identity_id):
            await handler(*args)


async def _run_until_handled_for_enabled_identities(handler, text, now, event, **handler_kwargs):
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        with use_identity(identity_id):
            if await handler(text, now, event, **handler_kwargs):
                return True
    return False


async def _run_claimed_prompt_handler(scope, handler, text, now, event, *, event_kind="message"):
    if not is_new_delivery(event_kind):
        return False
    if not _claim_runtime_event(event, scope=scope):
        return False
    return await _run_until_handled_for_enabled_identities(handler, text, now, event)


def _is_concubine_loss_broadcast_candidate(text):
    raw_text = str(text or "")
    return (
        "南陇侯" in raw_text
        and "侍妾" in raw_text
        and ("掳走" in raw_text or "选择将侍妾" in raw_text)
    )


async def _dispatch_new_message_broadcasts(event, text, now, reply_to=None, reply_context=None):
    await _dispatch_broadcast_handlers(
        event,
        text,
        now,
        _NEW_MESSAGE_BROADCAST_HANDLERS,
        reply_to=reply_to,
        reply_context=reply_context,
    )
    if _is_concubine_loss_broadcast_candidate(text) and _claim_runtime_event(event, scope="concubine_loss"):
        await _run_until_handled_for_enabled_identities(handle_concubine_loss_broadcast, text, now, event)


_NEW_MESSAGE_BROADCAST_HANDLERS = (
    ("deep_retreat_summary", handle_deep_retreat_summary_broadcast),
    ("yuanying_summary", handle_yuanying_summary_broadcast),
    ("realm_breakthrough", handle_realm_breakthrough_broadcast),
    ("quiz_result", handle_quiz_result_broadcast),
    ("quiz_learning_prompt", handle_quiz_learning_prompt),
    ("tianji_quiz_result", handle_tianji_quiz_result_broadcast),
    ("tianji_quiz_prompt", handle_tianji_quiz_prompt),
    ("tiandao_judgement_punishment", handle_tiandao_judgement_punishment),
    ("tiandao_judgement_prompt", handle_tiandao_judgement_prompt),
    ("guanxing_finish", handle_guanxing_finish_broadcast),
    ("ranch_return", handle_ranch_return_broadcast),
)
_BROADCAST_EVENT_HANDLERS = {
    handle_quiz_learning_prompt,
    handle_tianji_quiz_prompt,
    handle_tiandao_judgement_punishment,
    handle_tiandao_judgement_prompt,
    handle_ranch_return_broadcast,
}
_BROADCAST_REPLY_CONTEXT_HANDLERS = {
    handle_tianji_quiz_result_broadcast,
}
_PHASEFUL_SUMMARY_REPLY_CONTEXT_HANDLERS = {
    handle_deep_retreat_summary_broadcast,
    handle_yuanying_summary_broadcast,
}


async def _dispatch_broadcast_handlers(event, text, now, handlers, *, reply_to=None, reply_context=None):
    for scope, handler in handlers:
        if not _claim_runtime_event(event, scope=scope):
            continue
        if handler in _PHASEFUL_SUMMARY_REPLY_CONTEXT_HANDLERS:
            await handler(text, now, event=event, reply_to=reply_to, reply_context=reply_context)
        elif handler in _BROADCAST_REPLY_CONTEXT_HANDLERS:
            await handler(text, now, event, reply_to=reply_to)
        elif handler in _BROADCAST_EVENT_HANDLERS:
            await handler(text, now, event)
        else:
            await handler(text, now)


async def _dispatch_tree_broadcast_fallbacks(event, text, now):
    if _is_tree_runtime_archived():
        return
    if _claim_runtime_event(event, scope="tree_invasion_end"):
        await _run_for_all_identities(handle_tree_invasion_end, text, now, False)
    if _claim_runtime_event(event, scope="tree_invasion_start"):
        await _run_for_all_identities(handle_tree_invasion_start, text, now)
    if _claim_runtime_event(event, scope="tree_rebirth_reset"):
        await _run_for_all_identities(handle_tree_rebirth_reset, text, now)
    if _claim_runtime_event(event, scope="tree_panel"):
        await _run_for_all_identities(handle_tree_panel, text, now, False)


async def _dispatch_stargazer_broadcast_fallbacks(event, text, now):
    if _claim_runtime_event(event, scope="stargazer_panel"):
        await _run_for_all_identities(handle_stargazer_panel, text, now, False)


async def _dispatch_guanxing_monitor_broadcast_fallbacks(event, text, now):
    if _claim_runtime_event(event, scope="guanxing_monitor_broadcast"):
        await handle_guanxing_monitor_broadcast(text, now)


async def _dispatch_formation_broadcast_fallbacks(event, text, now, *, reply_to=None, reply_context=None, event_type="message"):
    if not is_formation_reply_text(text):
        return
    scope = "formation_event_edit" if event_type == "edit" else "formation_event"
    if _claim_runtime_event(event, scope=scope):
        await handle_formation_event(text, now, event, reply_to=reply_to, reply_context=reply_context)


async def _dispatch_small_world_broadcast_fallbacks(event, text, now):
    if _claim_runtime_event(event, scope="small_world_disaster"):
        await _run_until_handled_for_enabled_identities(handle_small_world_disaster_broadcast, text, now, event)


async def _dispatch_world_boss_broadcast_fallbacks(event, text, now):
    if _claim_runtime_event(event, scope="world_boss_event"):
        await handle_world_boss_broadcast(text, now, event=event)


async def _dispatch_duel_broadcast_fallbacks(event, text, now):
    raw_text = str(text or "")
    if not (
        raw_text.startswith("【天道战报·文字版】")
        or raw_text.startswith("【斗法终局】")
    ):
        return
    if _claim_runtime_event(event, scope="duel_broadcast"):
        await _run_until_handled_for_enabled_identities(handle_duel_broadcast, text, now, event)


async def _dispatch_nanlong_result_broadcast_fallbacks(event, text, now):
    if _claim_runtime_event(event, scope="nanlong_result"):
        await _run_until_handled_for_enabled_identities(handle_nanlong_result_broadcast, text, now, event)


async def _dispatch_concubine_affinity_fallbacks(event, text, now):
    if not is_concubine_affinity_event_candidate(text):
        return
    if _claim_runtime_event(event, scope="concubine_affinity"):
        await _run_until_handled_for_enabled_identities(
            handle_concubine_affinity_event,
            text,
            now,
            event,
            require_identity_hint=True,
        )


async def _dispatch_second_soul_broadcast_fallbacks(event, text, now):
    if _claim_runtime_event(event, scope="second_soul_return"):
        await handle_second_soul_return_broadcast(text, now)
    if _claim_runtime_event(event, scope="second_soul_heart_demon_warning"):
        await handle_second_soul_heart_demon_warning_broadcast(text, now, event.id)
    if _claim_runtime_event(event, scope="second_soul_choice_result"):
        await handle_second_soul_choice_result_broadcast(text, now)
    if _claim_runtime_event(event, scope="second_soul_recovery"):
        await handle_second_soul_recovery_broadcast(text, now)


async def _dispatch_message_edited_realm_breakthrough(event, text, now):
    await _dispatch_message_edited_broadcasts(event, text, now, _EARLY_MESSAGE_EDIT_BROADCAST_HANDLERS)


async def _dispatch_message_edited_tree_panel(event, text, now):
    if _is_tree_runtime_archived():
        return
    await _dispatch_message_edited_broadcasts(event, text, now, (("tree_panel_edit", handle_tree_panel),))


async def _dispatch_message_edited_stargazer_panel(event, text, now):
    await _dispatch_message_edited_broadcasts(event, text, now, (("stargazer_panel_edit", handle_stargazer_panel),))


async def _dispatch_message_edited_guanxing_monitor(event, text, now):
    await _dispatch_message_edited_broadcasts(event, text, now, (("guanxing_monitor_broadcast_edit", handle_guanxing_monitor_broadcast),))


async def _dispatch_message_edited_tiandao_judgement_prompt(event, text, now):
    await _dispatch_message_edited_broadcasts(event, text, now, (("tiandao_judgement_prompt_edit", handle_tiandao_judgement_prompt),))


async def _dispatch_message_edited_concubine_loss(event, text, now):
    if _is_concubine_loss_broadcast_candidate(text) and _claim_runtime_event(event, scope="concubine_loss"):
        await _run_until_handled_for_enabled_identities(handle_concubine_loss_broadcast, text, now, event)


async def _dispatch_message_edited_phaseful_summaries(event, text, now, reply_to=None, reply_context=None):
    await _dispatch_message_edited_broadcasts(
        event,
        text,
        now,
        _PHASEFUL_MESSAGE_EDIT_BROADCAST_HANDLERS,
        reply_to=reply_to,
        reply_context=reply_context,
    )


_EARLY_MESSAGE_EDIT_BROADCAST_HANDLERS = (
    ("realm_breakthrough_edit", handle_realm_breakthrough_broadcast),
)
_PHASEFUL_MESSAGE_EDIT_BROADCAST_HANDLERS = (
    ("deep_retreat_summary_edit", handle_deep_retreat_summary_broadcast),
    ("yuanying_summary_edit", handle_yuanying_summary_broadcast),
)
_MESSAGE_EDIT_IDENTITY_BROADCAST_HANDLERS = {
    handle_tree_panel,
    handle_stargazer_panel,
}
_MESSAGE_EDIT_EVENT_BROADCAST_HANDLERS = {
    handle_ranch_return_broadcast,
    handle_tiandao_judgement_prompt,
}


async def _dispatch_message_edited_broadcasts(event, text, now, handlers, *, reply_to=None, reply_context=None):
    for scope, handler in handlers:
        if not _claim_runtime_event(event, scope=scope):
            continue
        if handler in _PHASEFUL_SUMMARY_REPLY_CONTEXT_HANDLERS:
            await handler(text, now, event=event, reply_to=reply_to, reply_context=reply_context)
        elif handler in _MESSAGE_EDIT_IDENTITY_BROADCAST_HANDLERS:
            await _run_for_all_identities(handler, text, now, False)
        elif handler in _MESSAGE_EDIT_EVENT_BROADCAST_HANDLERS:
            await handler(text, now, event)
        else:
            await handler(text, now)


async def _run_identity_schedulers(now):
    await _run_phaseful_identity_schedulers(now)

    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        if _is_identity_account_offline(identity_id):
            continue
        with use_identity(identity_id):
            identity_now = time.time()
            if is_identity_weak(identity_id, identity_now):
                continue
            for scheduler in _PHASEFUL_IDENTITY_SCHEDULERS:
                await scheduler(identity_now)
            if has_phaseful_summary_block(identity_now):
                for scheduler in _PHASEFUL_BLOCK_CLEANUP_SCHEDULERS:
                    await scheduler(identity_now)
                continue
            for scheduler in _ORDINARY_IDENTITY_SCHEDULERS:
                await scheduler(identity_now)


async def _run_phaseful_identity_schedulers(now):
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        if _is_identity_account_offline(identity_id):
            continue
        with use_identity(identity_id):
            identity_now = time.time()
            scheduler_now = max(float(now or 0), identity_now)
            if is_identity_weak(identity_id, scheduler_now):
                continue
            for scheduler in _PHASEFUL_IDENTITY_SCHEDULERS:
                await scheduler(scheduler_now)


async def _run_phaseful_scheduler_loop(stop_event):
    while not stop_event.is_set():
        try:
            if get_global_enabled():
                await _run_phaseful_identity_schedulers(time.time())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print("phaseful scheduler loop crashed:")
            print("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            _fire_and_forget(
                send_audit_log(
                    f"❌ 结算续轮后台异常：{str(exc)[:180]}",
                    scope="global",
                    limit=300,
                )
            )
        await _sleep_or_stop(stop_event, 5)


async def _run_small_world_identity_schedulers(now):
    for identity_id in get_identity_ids():
        if not get_identity_enabled(identity_id):
            continue
        if _is_identity_account_offline(identity_id):
            continue
        with use_identity(identity_id):
            identity_now = time.time()
            scheduler_now = max(float(now or 0), identity_now)
            if is_identity_weak(identity_id, scheduler_now):
                continue
            if has_phaseful_summary_block(scheduler_now):
                continue
            await run_small_world_scheduler(scheduler_now)


async def _run_small_world_scheduler_loop(stop_event):
    while not stop_event.is_set():
        try:
            if get_global_enabled():
                await _run_small_world_identity_schedulers(time.time())
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print("small world scheduler loop crashed:")
            print("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
            _fire_and_forget(
                send_audit_log(
                    f"❌ 小世界后台调度异常：{str(exc)[:180]}",
                    scope="global",
                    limit=300,
                )
            )
        await _sleep_or_stop(stop_event, 10)


async def _run_global_schedulers(now):
    for name, scheduler in _GLOBAL_SCHEDULERS:
        if name == "delayed_actions":
            results = await scheduler(now, send_game_command)
            for result in results or ():
                send_as_id = int((result or {}).get("send_as_id") or 0)
                if send_as_id <= 0:
                    continue
                with use_identity(send_as_id):
                    await handle_jiyin_delayed_action_result(result)
        else:
            await scheduler(now)


async def _run_identity_schedulers_background(now):
    await _run_identity_schedulers(now)


def _handle_identity_scheduler_done(task):
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception as exc:
        print("identity scheduler crashed:")
        print("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        _fire_and_forget(
            send_audit_log(
                f"❌ 身份调度后台异常：{str(exc)[:180]}",
                scope="global",
                limit=300,
            )
        )


def _start_identity_schedulers_if_idle(now):
    global _identity_scheduler_task, _identity_scheduler_started_at, _identity_scheduler_last_warn_at
    if _identity_scheduler_task and not _identity_scheduler_task.done():
        if now - _identity_scheduler_started_at >= IDENTITY_SCHEDULER_STUCK_WARN_SEC and now - _identity_scheduler_last_warn_at >= IDENTITY_SCHEDULER_STUCK_WARN_SEC:
            _identity_scheduler_last_warn_at = now
            _fire_and_forget(
                send_audit_log(
                    "⏳ 身份调度仍在等待发送队列，主循环未阻塞，会继续按全局间隔串行发送。",
                    scope="global",
                    limit=260,
                )
            )
        return
    _identity_scheduler_started_at = float(now)
    _identity_scheduler_task = asyncio.create_task(_run_identity_schedulers_background(now))
    _identity_scheduler_task.add_done_callback(_handle_identity_scheduler_done)


def _cancel_identity_schedulers():
    global _identity_scheduler_task
    if _identity_scheduler_task and not _identity_scheduler_task.done():
        _identity_scheduler_task.cancel()


async def _handle_routed_reply_event(event, text, now, reply_to, reply_context, *, allow_tree_panel_claim=True, event_kind="message"):
    routed_identity_id = int((reply_context or {}).get("send_as_id") or 0)
    matched_family = (reply_context or {}).get("family") or None
    if routed_identity_id <= 0:
        return False

    family_scope = str(matched_family or "unknown").strip() or "unknown"
    kind_scope = str(event_kind or "message").strip() or "message"
    edit_text_scope = f":{hash(str(text or ''))}" if kind_scope == "edit" else ""
    if not _claim_runtime_event(event, scope=f"routed_reply:{kind_scope}:{routed_identity_id}:{family_scope}{edit_text_scope}"):
        return False

    # In multi-client mode a bot reply can be delivered to a different account
    # client than the one that sent the command. The reply_to message id is the
    # authoritative owner here; requiring the owner client would leave pending
    # tasks uncleared and trigger retry storms.
    allow_reprocessed_edit = kind_scope == "edit" and matched_family in {"concubine_heart", "divination", "explore_rift", "wendao", "wild_training"}
    already_consumed = bool(matched_family) and not allow_reprocessed_edit and _has_runtime_message_consumed(event, matched_family)
    with use_identity(routed_identity_id):
        is_reply_to_me = is_reply_to_identity_message(reply_to, routed_identity_id) or (
            int((reply_context or {}).get("reply_to_msg_id") or 0) > 0
            and int((reply_context or {}).get("send_as_id") or 0) == routed_identity_id
        )
        is_identity_info_observation = matched_family == "identity_info" and _is_identity_info_reply_observation(text)
        is_identity_info_waiting_reply = matched_family == "identity_info" and _is_identity_info_waiting_reply(text)
        is_nonterminal_waiting_reply = (
            matched_family in {"storage_bag_listing", "storage_bag_buy", "storage_bag_gift"}
            and is_storage_transfer_waiting_reply(text)
        ) or is_identity_info_waiting_reply
        clear_result = None if is_nonterminal_waiting_reply else clear_pending_by_reply(reply_to, routed_identity_id, reply_context=reply_context)
        root_msg_id = int((reply_context or {}).get("root_msg_id") or (clear_result or {}).get("reply_to_msg_id") or 0)
        if root_msg_id <= 0:
            root_msg_id = int(getattr(reply_to, "id", 0) or 0)
        if matched_family:
            track_reply_chain_message(event.id, routed_identity_id, matched_family, root_msg_id=root_msg_id)

        handled_any = False
        note_identity_weakness(text, now, routed_identity_id, source=matched_family or "reply")
        tree_runtime_archived = _is_tree_runtime_archived()
        if not tree_runtime_archived:
            await handle_tree_invasion_end(text, now, is_reply_to_me)
            await handle_tree_invasion_start(text, now)
            await handle_tree_rebirth_reset(text, now)
        if matched_family == "stargazer_sync":
            synced_panel = handle_stargazer_sync_reply(text, now=now)
            if synced_panel:
                declared_total_slots = int(synced_panel.get("declared_total_slots", 0) or 0)
                idle_slot_count = int(synced_panel.get("idle_slot_count", 0) or 0)
                dim_slot_count = int(synced_panel.get("dim_slot_count", 0) or 0)
                ready_slot_count = int(synced_panel.get("ready_slot_count", 0) or 0)
                max_wait = int(synced_panel.get("max_wait", 0) or 0)
                await send_audit_log(
                    f"🔭 已同步观星台[{routed_identity_id}]：总星盘 {declared_total_slots}，空闲 {idle_slot_count}，黯淡 {dim_slot_count}，精华已成 {ready_slot_count}，最长等待 {fmt_time_after(max_wait) if max_wait > 0 else '已无等待'}",
                    scope="identity",
                    send_as_id=routed_identity_id,
                )
                handled_any = True

        if allow_tree_panel_claim and not already_consumed and matched_family != "stargazer_sync":
            tree_panel_done = False
            if not tree_runtime_archived:
                tree_panel_done = await handle_tree_panel(text, now, is_reply_to_me)
            handled_any = handled_any or tree_panel_done
            stargazer_panel_done = await handle_stargazer_panel(text, now, is_reply_to_me, matched_family=matched_family)
            handled_any = handled_any or stargazer_panel_done
            if not tree_runtime_archived:
                handled_any = await handle_tree_harvest_reply(text, now, reply_to, matched_family=matched_family, current_msg_id=event.id) or handled_any

        if not already_consumed and matched_family != "stargazer_sync":
            handled_any = await _handle_replica_join_reply(text, now, reply_to, matched_family=matched_family, event=event) or handled_any
            if not tree_runtime_archived:
                handled_any = await handle_tree_cd_fix(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_pet_cd_fix(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_pet_warm_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_pet_trial_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_ranch_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_wild_training_reply(text, now, reply_to, matched_family=matched_family, current_msg_id=event.id) or handled_any
            handled_any = await handle_checkin_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_sect_teach_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_tower_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_stargazer_guide_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_stargazer_soothe_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_stargazer_collect_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_tianti_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_status_reply(text, now, reply_to, matched_family=matched_family, current_msg_id=event.id) or handled_any
            handled_any = await handle_concubine_dream_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_fragment_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_puzzle_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_reacquire_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_tianji_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_heart_reply(text, now, reply_to, matched_family=matched_family, current_msg_id=event.id) or handled_any
            handled_any = await handle_concubine_greet_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_storage_bag_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_gift_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_concubine_affinity_event(text, now, event, matched_family=matched_family) or handled_any
            handled_any = await handle_nanlong_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_guanxing_query_reply(text, now, reply_to, event.id, matched_family=matched_family) or handled_any
            handled_any = await handle_formation_event(text, now, event, reply_to=reply_to, reply_context=reply_context) or handled_any
            handled_any = await handle_identity_info_reply(text, now, reply_to, event.id) or handled_any
            deep_retreat_done = await handle_deep_retreat_success_reply(text, now, reply_to, matched_family=matched_family)
            handled_any = handled_any or deep_retreat_done
            if not deep_retreat_done:
                deep_retreat_done = await handle_deep_retreat_running_reply(text, now, reply_to, matched_family=matched_family)
                handled_any = handled_any or deep_retreat_done
            if not deep_retreat_done:
                handled_any = await handle_deep_retreat_status_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            yuanying_done = await handle_yuanying_success_reply(text, now, reply_to, matched_family=matched_family)
            handled_any = handled_any or yuanying_done
            if not yuanying_done:
                yuanying_done = await handle_yuanying_running_reply(text, now, reply_to, matched_family=matched_family)
                handled_any = handled_any or yuanying_done
            if not yuanying_done:
                handled_any = await handle_yuanying_status_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_wendao_reply(
                text,
                now,
                reply_to,
                matched_family=matched_family,
                result_msg_id=event.id,
            ) or handled_any
            handled_any = await handle_duel_reply(
                text,
                now,
                reply_to,
                matched_family=matched_family,
                result_msg_id=event.id,
            ) or handled_any
            handled_any = await handle_fishing_reply(
                text,
                now,
                reply_to,
                matched_family=matched_family,
                result_msg_id=event.id,
            ) or handled_any
            if not tree_runtime_archived:
                handled_any = await handle_tree_exception_prompt(text, now) or handled_any
            handled_any = await handle_small_world_preach_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_small_world_query_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_small_world_manifest_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_small_world_harvest_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_small_world_refine_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_small_world_barrier_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_explore_rift_reply(text, now, reply_to, matched_family=matched_family, result_msg_id=event.id) or handled_any
            handled_any = await handle_divination_reply(
                text,
                now,
                event=event,
                reply_to=reply_to,
                matched_family=matched_family,
                reply_context=reply_context,
            ) or handled_any
            handled_any = await handle_divination_exchange_reply(
                text,
                now,
                reply_to=reply_to,
                matched_family=matched_family,
                reply_context=reply_context,
            ) or handled_any
            handled_any = await handle_world_boss_reply(
                text,
                now,
                reply_to=reply_to,
                matched_family=matched_family,
                reply_context=reply_context,
                current_msg_id=event.id,
            ) or handled_any
            handled_any = await handle_second_soul_purge_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_second_soul_demon_status_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_second_soul_status_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_second_soul_train_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_taiyi_yindao_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_taiyi_node_search_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_taiyi_node_define_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            storage_transfer_done = await handle_storage_bag_transfer_reply(text, now, reply_to, matched_family=matched_family, reply_context=reply_context)
            handled_any = storage_transfer_done or handled_any
            if not storage_transfer_done:
                handled_any = await handle_storage_bag_reply(text, now, reply_to, matched_family=matched_family) or handled_any

        if matched_family and handled_any and not already_consumed:
            if matched_family != "concubine_heart" and not is_nonterminal_waiting_reply:
                close_action_guard_by_family(matched_family, send_as_id=routed_identity_id, reason="bot_reply_handled", now=now)
            _mark_runtime_message_consumed(event, matched_family)
        elif matched_family and not already_consumed and not is_nonterminal_waiting_reply:
            if is_identity_info_observation:
                pass
            elif _is_manual_storage_trade_observation(matched_family, reply_context):
                _record_manual_storage_trade_observation(
                    event,
                    text,
                    reply_context,
                    routed_identity_id=routed_identity_id,
                    event_kind=kind_scope,
                )
            else:
                record_unhandled_routed_reply(
                    from_telegram_event(
                        event,
                        text,
                        reply_context,
                        event_kind=kind_scope,
                        root_msg_id=root_msg_id,
                    )
                )

    await schedule_cleanup(reply_to, send_as_id=routed_identity_id)
    return handled_any


@client.on(events.NewMessage())
async def on_message(event):
    if _append_replica_group_message_log(event, event_type="message"):
        now = time.time()
        text = event.raw_text or ""
        if is_replica_group_command_text(text):
            try:
                if await _handle_replica_group_command(event):
                    return
            except Exception:
                print(traceback.format_exc())
        try:
            reply_to, reply_context = await _resolve_event_reply(event)
            await _handle_virtual_hall_auto_game_event(
                event,
                text,
                now,
                reply_to=reply_to,
                reply_context=reply_context,
                event_type="message",
            )
            await _handle_replica_progress_event(event, now, event_type="message")
        except Exception:
            print(traceback.format_exc())
        if is_replica_group_command_text(text):
            await _handle_replica_group_command(event)
        return
    if _append_replica_dispatch_group_message_log(event, event_type="message"):
        await _handle_replica_dispatch_group_command(event)
        return

    _append_game_group_message_log(event, event_type="message")

    if _claim_runtime_event(event, scope="log_group_command"):
        if await handle_log_group_command(event):
            return

    if event.chat_id != get_game_group_id():
        return
    now = time.time()
    sender_is_game_bot = await _is_game_bot_event(event)
    record_game_group_message(event, now=now, event_type="message")

    # bot 健康监测：记录 . 开头指令的触发时间
    raw_text = (event.raw_text or "").strip()
    sender_id = int(event.sender_id or 0)
    identity_sender_id = _resolve_identity_sender_id(sender_id)
    if raw_text.startswith(".") and identity_sender_id and get_global_enabled():
        note_game_command_observed(raw_text)

    if not sender_is_game_bot:
        text = event.raw_text or ""
        if identity_sender_id:
            observe_phaseful_identity_message(
                identity_sender_id,
                text,
                now=now,
                msg_id=event.id,
                reply_to=int(getattr(event, "reply_to_msg_id", 0) or 0),
            )
            _track_manual_game_command(identity_sender_id, text, event.id)
        try:
            await handle_dungeon_join_mention(event, text, now)
            await _handle_replica_progress_event(event, now, event_type="message")
            if await _handle_suspected_game_bot_reply(event, text, now):
                return
            if _claim_runtime_event(event, scope="guanxing_external_shift"):
                await handle_guanxing_external_shift_command(text, now, event)
        except Exception:
            print(traceback.format_exc())
        return

    # bot 健康监测：bot 有发言后，暂停态先探测，再恢复
    await _note_game_bot_activity()

    text = event.raw_text or ""
    observe_dungeon_quiet_text(text, now=now)

    try:
        reply_to, reply_context = await _resolve_event_reply(event)
        _record_message_box_shadow(
            event,
            text,
            reply_context,
            reply_to=reply_to,
            event_type="message",
            is_game_bot=sender_is_game_bot,
            is_game_group=True,
        )

        await _dispatch_new_message_broadcasts(event, text, now, reply_to=reply_to, reply_context=reply_context)
        await handle_huanglong_conscription_text(text, now)
        await handle_dungeon_join_bot_message(event, text, now)
        _mark_replica_team_joined_from_text(text, now, msg_id=getattr(event, "id", 0))
        await _handle_virtual_hall_auto_game_event(event, text, now, reply_to=reply_to, reply_context=reply_context, event_type="message")
        await _handle_replica_progress_event(event, now, event_type="message")

        if await _run_claimed_prompt_handler("quiz_prompt", handle_quiz_prompt, text, now, event):
            return

        if await _run_claimed_prompt_handler("jiyin_prompt", handle_jiyin_prompt, text, now, event):
            return

        if await _run_claimed_prompt_handler("nanlong_prompt", handle_nanlong_prompt, text, now, event):
            return

        if int((reply_context or {}).get("send_as_id") or 0) > 0:
            handled_reply = await _handle_routed_reply_event(event, text, now, reply_to, reply_context)
            if handled_reply:
                await handle_passive_module_card(
                    from_telegram_event(event, text, _handled_reply_context(reply_context), event_kind="message"),
                    now,
                )
                return

        if await _dispatch_fishing_swallowed_reply_fallback(event, text, now, event_kind="message"):
            await handle_passive_module_card(from_telegram_event(event, text, {"routed_reply_handled": True, "family": "fishing"}, event_kind="message"), now)
            return

        await _dispatch_tree_broadcast_fallbacks(event, text, now)
        await _dispatch_stargazer_broadcast_fallbacks(event, text, now)
        await _dispatch_guanxing_monitor_broadcast_fallbacks(event, text, now)
        await _dispatch_formation_broadcast_fallbacks(event, text, now, reply_to=reply_to, reply_context=reply_context, event_type="message")
        await _dispatch_small_world_broadcast_fallbacks(event, text, now)
        await _dispatch_world_boss_broadcast_fallbacks(event, text, now)
        await _dispatch_duel_broadcast_fallbacks(event, text, now)
        await _dispatch_nanlong_result_broadcast_fallbacks(event, text, now)
        await _dispatch_concubine_affinity_fallbacks(event, text, now)
        await _dispatch_second_soul_broadcast_fallbacks(event, text, now)
        await handle_passive_identity_profile_card(text, now)
        await handle_passive_module_card(from_telegram_event(event, text, reply_context, event_kind="message"), now)
        await handle_storage_bag_reply(text, now, reply_to)

    except Exception:
        print(traceback.format_exc())


@client.on(events.MessageEdited())
async def on_message_edited(event):
    if _append_replica_group_message_log(event, event_type="edit"):
        now = time.time()
        text = event.raw_text or ""
        try:
            reply_to, reply_context = await _resolve_event_reply(event)
            await _handle_virtual_hall_auto_game_event(
                event,
                text,
                now,
                reply_to=reply_to,
                reply_context=reply_context,
                event_type="edit",
            )
            await _handle_replica_progress_event(event, now, event_type="edit")
        except Exception:
            print(traceback.format_exc())
        return
    if _append_replica_dispatch_group_message_log(event, event_type="edit"):
        return

    _append_game_group_message_log(event, event_type="edit")

    if event.chat_id != get_game_group_id():
        return
    sender_is_game_bot = await _is_game_bot_event(event)
    if not sender_is_game_bot:
        try:
            now = time.time()
            text = event.raw_text or ""
            await _handle_replica_progress_event(event, now, event_type="edit")
            await _handle_suspected_game_bot_reply(event, text, now, edited=True)
        except Exception:
            print(traceback.format_exc())
        return

    await _note_game_bot_activity()

    now = time.time()
    text = event.raw_text or ""
    observe_dungeon_quiet_text(text, now=now)

    try:
        reply_to, reply_context = await _resolve_event_reply(event)
        _record_message_box_shadow(
            event,
            text,
            reply_context,
            reply_to=reply_to,
            event_type="edit",
            is_game_bot=sender_is_game_bot,
            is_game_group=True,
        )

        await _dispatch_message_edited_realm_breakthrough(event, text, now)
        await _dispatch_message_edited_concubine_loss(event, text, now)
        await _dispatch_message_edited_phaseful_summaries(
            event,
            text,
            now,
            reply_to=reply_to,
            reply_context=reply_context,
        )
        await handle_huanglong_conscription_text(text, now)
        await handle_dungeon_join_bot_message(event, text, now)
        _mark_replica_team_joined_from_text(text, now, msg_id=getattr(event, "id", 0))
        await _handle_virtual_hall_auto_game_event(event, text, now, reply_to=reply_to, reply_context=reply_context, event_type="edit")
        await _handle_replica_progress_event(event, now, event_type="edit")

        if int((reply_context or {}).get("send_as_id") or 0) > 0:
            handled_reply = await _handle_routed_reply_event(
                event,
                text,
                now,
                reply_to,
                reply_context,
                event_kind="edit",
            )
            if handled_reply:
                await handle_passive_module_card(
                    from_telegram_event(event, text, _handled_reply_context(reply_context), event_kind="edit"),
                    now,
                )
                return

        if await _dispatch_fishing_swallowed_reply_fallback(event, text, now, event_kind="edit"):
            await handle_passive_module_card(from_telegram_event(event, text, {"routed_reply_handled": True, "family": "fishing"}, event_kind="edit"), now)
            return

        if await handle_divination_reply(
            text,
            now,
            event=event,
            reply_to=reply_to,
            matched_family=None,
            reply_context=reply_context,
        ):
            await handle_passive_module_card(from_telegram_event(event, text, reply_context, event_kind="edit"), now)
            return

        await _dispatch_message_edited_tree_panel(event, text, now)
        await _dispatch_message_edited_stargazer_panel(event, text, now)
        await _dispatch_message_edited_guanxing_monitor(event, text, now)
        await _dispatch_formation_broadcast_fallbacks(event, text, now, reply_to=reply_to, reply_context=reply_context, event_type="edit")
        await _dispatch_world_boss_broadcast_fallbacks(event, text, now)
        await _dispatch_duel_broadcast_fallbacks(event, text, now)
        await _dispatch_message_edited_tiandao_judgement_prompt(event, text, now)
        await _dispatch_message_edited_broadcasts(event, text, now, (("ranch_return_edit", handle_ranch_return_broadcast),))
        await _dispatch_concubine_affinity_fallbacks(event, text, now)
        await _dispatch_second_soul_broadcast_fallbacks(event, text, now)
        await handle_passive_identity_profile_card(text, now)
        await handle_passive_module_card(from_telegram_event(event, text, reply_context, event_kind="edit"), now)
    except Exception:
        print(traceback.format_exc())


def _register_event_handlers(tc):
    tc.add_event_handler(on_message, events.NewMessage())
    tc.add_event_handler(on_message_edited, events.MessageEdited())


async def bootstrap():
    loaded = load_state()
    if not loaded and has_persisted_identity_rows():
        raise RuntimeError("SQLite 状态加载失败，已阻止首次初始化以避免覆盖既有身份计时器。")
    saved_accounts = get_accounts()

    # 多账号模式下只启动账号 client，避免主 session 也挂一个空转 Telegram 会话。
    # 没有保存账号时保留旧的单账号主 session 启动路径。
    if not saved_accounts:
        await client.connect()

    # 启动已保存的额外账号 client
    failed_accounts = []
    for acct_id_str, acct_info in saved_accounts.items():
        acct_id = 0
        tc = None
        try:
            acct_id = int(acct_id_str)
            acct_info = acct_info if isinstance(acct_info, dict) else {}
            tc = create_account_client(
                acct_id,
                api_id=acct_info.get("api_id"),
                api_hash=acct_info.get("api_hash"),
            )
            await tc.connect()
            if not await tc.is_user_authorized():
                error_text = "session 未授权，请通过 UI 重新登录账号"
                mark_account_offline(acct_id, error_text)
                failed_accounts.append({"account_id": acct_id, "error": error_text})
                print(f"启动额外账号 {acct_id_str} 跳过: {error_text}")
                try:
                    await tc.disconnect()
                except Exception:
                    pass
                continue
            try:
                await tc.get_dialogs()
            except Exception:
                pass
            register_client(acct_id, tc)
            _register_event_handlers(tc)
        except Exception:
            tb = traceback.format_exc()
            error_text = tb.strip().splitlines()[-1] if tb.strip() else "启动失败"
            mark_account_offline(acct_id_str, error_text)
            failed_accounts.append({"account_id": acct_id or int(acct_id_str), "error": error_text})
            if tc is not None and tc.is_connected():
                try:
                    await tc.disconnect()
                except Exception:
                    pass
            print(f"启动额外账号 {acct_id_str} 失败: {tb}")

    await start_ui_server()

    # 获取 my_user_id：优先主 client，再尝试已登录账号
    if client.is_connected():
        try:
            _me = await client.get_me()
            if _me:
                state["my_user_id"] = _me.id
                if get_registered_client(_me.id) is None:
                    register_client(_me.id, client)
        except Exception:
            pass
    if not state.get("my_user_id"):
        for _acct_id_str in get_accounts():
            try:
                _account_id = int(_acct_id_str)
                if is_account_offline(_account_id):
                    continue
                _tc = get_registered_client(_account_id)
                if _tc is None:
                    continue
                _me = await _tc.get_me()
                if _me:
                    state["my_user_id"] = _me.id
                    break
            except Exception:
                pass

    identity_ids = get_identity_ids()
    startup_account_check_result = run_startup_account_integrity_check(identity_ids, failed_accounts)
    runtime_account_ids = [
        int(account_id)
        for account_id in get_all_clients().keys()
        if int(account_id or 0) > 0 and not is_account_offline(account_id)
    ]
    single_runtime_account_id = runtime_account_ids[0] if len(runtime_account_ids) == 1 else 0
    for send_as_id in identity_ids:
        try:
            account_id = get_identity_account(send_as_id) or single_runtime_account_id
            if not account_id:
                print(f"hydrate_identity_profile skipped (no account): {send_as_id}")
                continue
            if is_account_offline(account_id):
                print(f"hydrate_identity_profile skipped (offline account): {send_as_id} acc={account_id}")
                continue
            tc = get_registered_client(account_id)
            if tc is None:
                print(f"hydrate_identity_profile skipped (account client missing): {send_as_id} acc={account_id}")
                continue
            send_as_entity = await tc.get_entity(send_as_id)
            hydrate_identity_profile(send_as_entity)
        except Exception:
            print(f"hydrate_identity_profile failed: {send_as_id}")
        enforce_identity_module_availability(send_as_id, persist=False)

    now = time.time()
    clear_expired_dungeon_quiet(now)
    if loaded:
        _cleanup_replica_run_state(now)
        mark_dirty()
    paused_at_boot = not get_global_enabled()
    if paused_at_boot:
        console_log("🚀 自动化系统启动：全局暂停中，仅加载状态与 UI，跳过启动恢复和普通调度。")
        return

    if state.get("guanxing_monitor_enabled"):
        restore_guanxing_monitor_runtime_state(now)
        mark_dirty()
    _round_state, round_changed = restore_guanxing_round_runtime(now)
    if round_changed:
        mark_dirty()
    startup_scan_result = scan_startup_timeout_tasks(now) if loaded else {"closed_count": 0, "affected_identity_ids": [], "alerts": []}
    any_loaded = False
    if loaded:
        for identity_id in identity_ids:
            identity_state = get_identity_state(identity_id)
            if any(identity_state.get(timer_key, 0) > 0 for timer_key in IDENTITY_TIMER_COLUMNS):
                any_loaded = True
                break

    if not any_loaded:
        for identity_id in identity_ids:
            initialize_identity_runtime(identity_id, now)
        spread_overdue_runtime_timers(now, reason="启动初始化")
        save_state()
    else:
        for identity_id in identity_ids:
            initialize_identity_runtime(identity_id, now)
            mark_dirty()
        spread_overdue_runtime_timers(now, reason="启动恢复")
        save_state()

    identity_lines = [
        f"- {send_as_id}: {mono('@' + (get_send_as_profile(send_as_id).get('username') or '未获取到'))}"
        for send_as_id in identity_ids
    ]
    recovery_text = "成功" if any_loaded else ("无待恢复任务" if loaded else "首次初始化")
    audit_lines = [
        "🚀 自动化系统启动成功",
        f"👤 账号: {state.get('my_user_id') or '未登录（等待 UI 登录）'}",
        f"🎭 并发身份数: {len(identity_ids)}",
        "📡 模式: 多身份 + SQLite 持久化",
        f"💾 状态恢复: {recovery_text}",
        "🪪 身份列表:",
        *identity_lines,
    ]
    if any_loaded:
        audit_lines.extend([
            "♻️ 启动恢复：检测到本地状态，已按 SQLite 中的多身份状态恢复运行。",
            "📌 本次为恢复模式启动：不会执行全量探测，只按各身份本地状态与时间继续调度。",
        ])
    if startup_scan_result.get("closed_count", 0) > 0:
        audit_lines.append(
            f"⚠️ 启动扫描：发现超时任务并自动关闭 {startup_scan_result['closed_count']} 个模块，登录 UI 后可手动恢复。"
        )
    audit_lines.extend(startup_account_check_result.get("audit_lines") or [])
    _fire_and_forget(send_audit_log("\n".join(audit_lines), scope="global", limit=1200))


async def _sleep_or_stop(stop_event, delay):
    if stop_event is None:
        await asyncio.sleep(delay)
        return
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=delay)
    except asyncio.TimeoutError:
        pass


async def main_loop(stop_event=None):
    while stop_event is None or not stop_event.is_set():
        now = time.time()

        gc_my_msg_ids(now)
        gc_ui_login_tokens(now)
        gc_ui_sessions(now)
        flushed_ok = flush_if_dirty(now)
        if (flushed_ok is False or has_persistence_write_failure()) and get_global_enabled():
            _cancel_identity_schedulers()
            failure = get_persistence_write_failure()
            error_text = failure.get("error") or "unknown"
            await toggle_global_enabled(False, source="persistence_guard")
            await send_audit_log(
                f"💾 SQLite 状态保存失败，已暂停普通自动化，避免继续使用不可信计时器：{error_text}",
                scope="global",
                limit=360,
            )
            await _sleep_or_stop(stop_event, 5)
            continue

        # bot 健康监测：疑似静默/探测中直接全局暂停，避免继续普通发送
        global _bot_silence_auto_paused
        check_bot_health_timeout(now, BOT_SILENCE_TIMEOUT_SEC)
        if should_pause_for_bot_health() and get_global_enabled():
            _bot_silence_auto_paused = True
            _cancel_identity_schedulers()
            clear_all_pending_tasks("天尊健康暂停")
            await toggle_global_enabled(False, source="bot_health_monitor")
        await run_rare_daily_report_scheduler(now)
        if not get_global_enabled():
            _cancel_identity_schedulers()
            await _sleep_or_stop(stop_event, 5)
            continue

        await _run_phaseful_identity_schedulers(time.time())
        await _run_global_schedulers(now)
        await run_quiz_learning_scheduler(now)
        await run_retry_scheduler(now)
        await run_identity_info_followup_scheduler(now)
        await _run_phaseful_identity_schedulers(time.time())
        _start_identity_schedulers_if_idle(now)
        await _sleep_or_stop(stop_event, 5)


async def main():
    global _log_bot_callback_task, _phaseful_scheduler_task, _small_world_scheduler_task
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_stop():
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda _signum, _frame: request_stop())

    try:
        await bootstrap()
        _log_bot_callback_task = asyncio.create_task(
            run_log_bot_callback_poller(handle_replica_button_callback, stop_event)
        )
        _phaseful_scheduler_task = asyncio.create_task(_run_phaseful_scheduler_loop(stop_event))
        _small_world_scheduler_task = asyncio.create_task(_run_small_world_scheduler_loop(stop_event))
        await main_loop(stop_event)
    finally:
        await shutdown()


async def shutdown():
    global _log_bot_callback_task, _phaseful_scheduler_task, _small_world_scheduler_task
    _cancel_identity_schedulers()
    if _phaseful_scheduler_task and not _phaseful_scheduler_task.done():
        _phaseful_scheduler_task.cancel()
        try:
            await _phaseful_scheduler_task
        except asyncio.CancelledError:
            pass
    _phaseful_scheduler_task = None
    if _small_world_scheduler_task and not _small_world_scheduler_task.done():
        _small_world_scheduler_task.cancel()
        try:
            await _small_world_scheduler_task
        except asyncio.CancelledError:
            pass
    _small_world_scheduler_task = None
    if _log_bot_callback_task and not _log_bot_callback_task.done():
        _log_bot_callback_task.cancel()
        try:
            await _log_bot_callback_task
        except asyncio.CancelledError:
            pass
    _log_bot_callback_task = None
    save_state()
    await stop_ui_server()
    clients = [client]
    clients.extend(get_all_clients().values())
    seen = set()
    for tc in clients:
        if tc is None or id(tc) in seen:
            continue
        seen.add(id(tc))
        try:
            await tc.disconnect()
        except Exception:
            traceback.print_exc()


__all__ = ["bootstrap", "main", "main_loop", "on_message", "shutdown"]
