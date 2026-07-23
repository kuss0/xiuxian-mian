import asyncio
import re
import signal
import time
import traceback
from types import SimpleNamespace

from telethon import events, functions
from telethon.errors import PeerIdInvalidError, SendAsPeerInvalidError

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
    observe_replica_game_command_message,
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
from .control import clear_transient_send_failures_for_global_recovery, enforce_identity_module_availability, extend_global_recovery_throttle_for_spread, handle_identity_info_reply, handle_log_group_command, handle_passive_identity_profile_card, handle_realm_breakthrough_broadcast, hydrate_identity_profile, initialize_identity_runtime, register_message_box_shadow_payload_provider, run_identity_info_followup_scheduler, run_startup_account_integrity_check, scan_startup_timeout_tasks, spread_overdue_runtime_timers, toggle_global_enabled
from .game_bot_registry import GameBotCandidateRegistry
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
from .features.divination import handle_divination_exchange_reply, handle_divination_reply, recover_divination_startup_timeouts, run_divination_scheduler
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
    concubine_send_queue_timeout,
    CONCUBINE_DUE_SCAN_SEND_QUEUE_TIMEOUT_SEC,
    restore_concubine_runtime,
    run_concubine_phaseful_cleanup_scheduler,
    run_concubine_scheduler,
)
from .features.pet import handle_pet_cd_fix, handle_pet_warm_reply, handle_pet_trial_reply, handle_pet_formation_reply, run_pet_scheduler
from .features.passive_inbox import handle_passive_module_card, record_passive_inbox_event
from .command_attempt import bind_shadow_evidence
from .features.ranch import handle_ranch_reply, handle_ranch_return_broadcast, run_ranch_scheduler
from .features.rare_daily_report import run_rare_daily_report_scheduler
from .features.duel_daily_report import run_duel_daily_report_scheduler
from .features.jiyin import handle_jiyin_delayed_action_result, handle_jiyin_prompt, run_jiyin_scheduler
from .features.join_dungeon import handle_dungeon_join_bot_message, handle_dungeon_join_mention, record_game_group_message
from .features.nanlong import handle_nanlong_prompt, handle_nanlong_reply, handle_nanlong_result_broadcast, run_nanlong_scheduler
from .features.quiz import handle_quiz_learning_prompt, handle_quiz_prompt, handle_quiz_result_broadcast, run_quiz_learning_scheduler, run_quiz_scheduler
from .features.tianti import handle_tianti_reply, run_tianti_scheduler
from .features.tiandao_judgement import handle_tiandao_judgement_prompt, handle_tiandao_judgement_punishment, run_tiandao_judgement_scheduler
from .features.tianji_quiz import handle_tianji_quiz_prompt, handle_tianji_quiz_result_broadcast, run_tianji_quiz_scheduler
from .features.cave_treasure_runtime import handle_cave_treasure_miniapp_entry
from .features.trial_runtime import handle_trial_miniapp_entry
from .features.tree_runtime import handle_tree_miniapp_entry
from .features.tianxing import (
    apply_tianxing_passive,
    build_tianxing_consume_window,
    build_tianxing_route_preflight_plan,
    has_tianxing_craft_farm_due,
    has_tianxing_craft_farm_override_due,
    has_tianxing_timeline_due_work,
    is_tianxing_route_released,
    normalize_tianxing_observation,
    normalize_tianxing_timeline_state,
    run_tianxing_daily_bootstrap_scheduler,
    run_tianxing_scheduler,
    run_tianxing_timeline_scheduler,
    run_tianxing_timeline_followup_scheduler,
)
from .features.yinluo import run_yinluo_scheduler
from .features.mulan import handle_mulan_reply, run_mulan_scheduler
from .features.wanxin import handle_wanxin_reply, run_wanxin_global_cleanup_scheduler, run_wanxin_phaseful_cleanup_scheduler, run_wanxin_scheduler
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
    handle_stargazer_miniapp_entry,
    handle_stargazer_panel,
    handle_stargazer_soothe_reply,
    handle_stargazer_sync_reply,
)
from .features.storage_bag import handle_storage_bag_reply, handle_storage_bag_transfer_reply, is_storage_transfer_waiting_reply, run_storage_bag_transfer_scheduler
from .features.tower import run_tower_scheduler
from .features.explore_rift import handle_explore_rift_reply, run_explore_rift_scheduler
from .features.tree import (
    handle_tree_cd_fix,
    handle_tree_exception_prompt,
    handle_tree_harvest_reply,
    handle_tree_invasion_end,
    handle_tree_invasion_start,
    handle_tree_panel,
    handle_tree_rebirth_reset,
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
from .features.duel import handle_duel_broadcast, handle_duel_reply, handle_duel_target_observation, run_duel_scheduler
from .features.fishing_runtime import (
    handle_fishing_miniapp_entry,
    handle_fishing_reply,
    hold_unclaimed_fishing_miniapp_entry,
    is_fishing_reply_text,
    run_fishing_scheduler,
)
from .features.wild_training import (
    WILD_TRAINING_RETRY_MAX_SEC,
    WILD_TRAINING_RETRY_MIN_SEC,
    WILD_TRAINING_SCHEDULER_TIMEOUT_SEC,
    _tianxing_prepare_retry_blocks,
    reconcile_wild_training_daily_reset_spread,
    run_wild_training_scheduler,
)
from .features.red_packet_monitor import observe_red_packet_candidate
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
from .message_log_recovery import find_message_log_replies_tail
from .message_box import (
    MessageBox,
    build_message_box_snapshot_payload,
    build_message_fact_from_event,
    write_message_box_snapshot_payload,
)
from .verified_event import from_telegram_event, is_new_delivery
from .runtime import (
    MAINTENANCE_PAUSE_SOURCE,
    _fire_and_forget,
    CHANNEL_SEND_AS_PROBE_INTERVAL_SEC,
    account_rpc_slot,
    check_bot_health_timeout,
    clear_pending_by_reply,
    _clear_channel_send_as_invalid_observations,
    _clear_send_as_peer_invalid,
    console_log,
    GAME_SEND_RPC_TIMEOUT_SEC,
    GAME_SEND_TIMEOUT_RECOVERY_WAIT_SEC,
    GLOBAL_RECOVERY_THROTTLE_SEND_GAP_MAX_SEC,
    gc_my_msg_ids,
    gc_ui_login_tokens,
    gc_ui_sessions,
    get_reply_context,
    is_identity_weak,
    is_account_session_error,
    is_reply_to_identity_message,
    mark_bot_health_recovered,
    note_bot_health_probe_attempt,
    note_game_bot_message,
    note_game_command_observed,
    note_identity_weakness,
    register_game_command_sent_observer,
    resolve_reply_family,
    restore_bot_health_auto_pause,
    run_retry_scheduler,
    schedule_cleanup,
    send_game_command,
    set_game_send_quiesced,
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
    get_game_listener_account_ids,
    get_global_enabled,
    get_global_pause_source,
    get_channel_send_as_health,
    get_identity_account,
    get_identity_enabled,
    get_identity_ids,
    get_identity_state,
    get_send_as_profile,
    set_channel_send_as_health,
    set_identity_enabled,
    set_game_bot_ids,
    state,
    update_send_as_profile,
    use_identity,
)
from .timing import fmt_time_after


_EARLY_ROUTED_REPLY_TTL_SEC = 30.0
_EARLY_ROUTED_REPLY_REPLAY_DELAY_SEC = 0.1
_EARLY_ROUTED_REPLY_MAX = 512
_early_routed_replies = {}
from .ui import run_miniapp_daily_scheduler, run_storage_bag_api_keepalive_scheduler, start_ui_server, stop_ui_server

_bot_silence_auto_paused = False
_identity_scheduler_task = None
_identity_scheduler_started_at = 0.0
_identity_scheduler_last_warn_at = 0.0
_log_bot_callback_task = None
_phaseful_scheduler_task = None
_small_world_scheduler_task = None
_observed_game_commands = {}
_MESSAGE_BOX_SHADOW_CAP = 10000
_message_box_shadow = MessageBox(cap=_MESSAGE_BOX_SHADOW_CAP)

IDENTITY_SCHEDULER_STUCK_WARN_SEC = 15 * 60
UNKNOWN_GAME_BOT_LEARN_THRESHOLD = 6
UNKNOWN_GAME_BOT_HIT_TTL_SEC = 5 * 60
UNKNOWN_GAME_BOT_EXTERNAL_LEARN_MIN_REPLIES = 6
UNKNOWN_GAME_BOT_EXTERNAL_LEARN_MIN_PLAYERS = 3
UNKNOWN_GAME_BOT_EXTERNAL_LEARN_MIN_COMMANDS = 3
_game_bot_candidate_registry = GameBotCandidateRegistry(
    ttl_sec=UNKNOWN_GAME_BOT_HIT_TTL_SEC,
    min_replies=UNKNOWN_GAME_BOT_EXTERNAL_LEARN_MIN_REPLIES,
    min_players=UNKNOWN_GAME_BOT_EXTERNAL_LEARN_MIN_PLAYERS,
    min_commands=UNKNOWN_GAME_BOT_EXTERNAL_LEARN_MIN_COMMANDS,
)
_suspected_game_bot_hits = _game_bot_candidate_registry.candidates
OBSERVED_GAME_COMMAND_TTL_SEC = 15 * 60
OBSERVED_GAME_COMMAND_CAP = 2000
HAN_TIANZUN_BOT_NAME = "韩天尊"
TIANXING_DAILY_BOOTSTRAP_MAX_PER_TICK = 2
TIANXING_TIMELINE_FOLLOWUP_MAX_PER_TICK = 4
DUE_WILD_TRAINING_MAX_PER_TICK = 5
DUE_SCAN_TIMEOUT_MARGIN_SEC = 45
DUE_RECOVERY_SEND_QUEUE_TIMEOUT_SEC = (
    GAME_SEND_RPC_TIMEOUT_SEC
    + GAME_SEND_TIMEOUT_RECOVERY_WAIT_SEC
    + GLOBAL_RECOVERY_THROTTLE_SEND_GAP_MAX_SEC
    + DUE_SCAN_TIMEOUT_MARGIN_SEC
)
DUE_WILD_TRAINING_SCHEDULER_TIMEOUT_SEC = WILD_TRAINING_SCHEDULER_TIMEOUT_SEC + DUE_SCAN_TIMEOUT_MARGIN_SEC
DUE_WILD_TRAINING_DIAG_INTERVAL_SEC = 120
_due_wild_training_last_diag_at = 0.0
DUE_EXPLORE_RIFT_MAX_PER_TICK = 3
DUE_EXPLORE_RIFT_SCHEDULER_TIMEOUT_SEC = max(90, DUE_RECOVERY_SEND_QUEUE_TIMEOUT_SEC)
DUE_EXPLORE_RIFT_DIAG_INTERVAL_SEC = 120
_due_explore_rift_last_diag_at = 0.0
DUE_CONCUBINE_MAX_PER_TICK = 1
DUE_CONCUBINE_SCHEDULER_TIMEOUT_SEC = CONCUBINE_DUE_SCAN_SEND_QUEUE_TIMEOUT_SEC + DUE_SCAN_TIMEOUT_MARGIN_SEC
DUE_CONCUBINE_DIAG_INTERVAL_SEC = 180
_due_concubine_last_diag_at = 0.0
DUE_TIANXING_MAX_PER_TICK = 2
DUE_TIANXING_SCHEDULER_TIMEOUT_SEC = max(90, DUE_RECOVERY_SEND_QUEUE_TIMEOUT_SEC)
DUE_TIANXING_DIAG_INTERVAL_SEC = 180
_due_tianxing_last_diag_at = 0.0


async def run_channel_send_as_health_scheduler(now):
    record = dict(get_channel_send_as_health())
    if str(record.get("status") or "") != "closed":
        return
    now = float(now or time.time())
    if float(record.get("next_probe_at", 0) or 0) > now:
        return
    account_id = int(record.get("account_id") or 0)
    game_group_id = int(get_game_group_id() or record.get("game_group_id") or 0)
    restore_identity_ids = sorted({
        int(identity_id)
        for identity_id in record.get("restore_identity_ids") or []
        if int(identity_id or 0) > 0
    })
    client_obj = get_registered_client(account_id) if account_id > 0 else None
    if client_obj is None or is_account_offline(account_id) or game_group_id == 0:
        record.update({
            "last_probe_at": now,
            "next_probe_at": now + CHANNEL_SEND_AS_PROBE_INTERVAL_SEC,
            "last_error": "probe_client_unavailable",
        })
        set_channel_send_as_health(record)
        mark_dirty()
        return
    try:
        if not restore_identity_ids:
            raise ValueError("missing frozen channel identities")
        async with account_rpc_slot(account_id=account_id, client_obj=client_obj):
            peer = await asyncio.wait_for(client_obj.get_input_entity(game_group_id), timeout=20)
            restored_identity_ids = []
            try:
                result = await asyncio.wait_for(
                    client_obj(functions.channels.GetSendAsRequest(peer=peer)),
                    timeout=20,
                )
                available_identity_ids = set()
                for send_as_peer in getattr(result, "peers", ()) or ():
                    peer_obj = getattr(send_as_peer, "peer", None)
                    for field in ("channel_id", "user_id", "chat_id"):
                        peer_id = int(getattr(peer_obj, field, 0) or 0)
                        if peer_id > 0:
                            available_identity_ids.add(peer_id)
                            break
                restored_identity_ids = [
                    identity_id
                    for identity_id in restore_identity_ids
                    if identity_id in available_identity_ids
                ]
            except PeerIdInvalidError:
                # Some supergroups reject GetSendAs while still accepting a
                # valid SaveDefaultSendAs. Probe each frozen identity so one
                # stale channel cannot keep the whole cohort disabled.
                for identity_id in restore_identity_ids:
                    try:
                        probe_send_as = await asyncio.wait_for(
                            client_obj.get_input_entity(identity_id),
                            timeout=20,
                        )
                        await asyncio.wait_for(
                            client_obj(functions.messages.SaveDefaultSendAsRequest(peer=peer, send_as=probe_send_as)),
                            timeout=20,
                        )
                    except (PeerIdInvalidError, SendAsPeerInvalidError):
                        continue
                    restored_identity_ids.append(identity_id)
                if restored_identity_ids:
                    personal_send_as = await asyncio.wait_for(client_obj.get_input_entity(account_id), timeout=20)
                    await asyncio.wait_for(
                        client_obj(functions.messages.SaveDefaultSendAsRequest(peer=peer, send_as=personal_send_as)),
                        timeout=20,
                    )
            if not restored_identity_ids:
                raise SendAsPeerInvalidError(request=None)
    except (PeerIdInvalidError, SendAsPeerInvalidError) as exc:
        record.update({
            "game_group_id": game_group_id,
            "last_probe_at": now,
            "next_probe_at": now + CHANNEL_SEND_AS_PROBE_INTERVAL_SEC,
            "last_error": type(exc).__name__,
        })
        set_channel_send_as_health(record)
        mark_dirty()
        return
    except Exception as exc:
        record.update({
            "game_group_id": game_group_id,
            "last_probe_at": now,
            "next_probe_at": now + CHANNEL_SEND_AS_PROBE_INTERVAL_SEC,
            "last_error": f"{type(exc).__name__}: {str(exc)[:120]}",
        })
        set_channel_send_as_health(record)
        mark_dirty()
        return

    restored = 0
    for identity_id in restored_identity_ids:
        _clear_send_as_peer_invalid(
            identity_id,
            account_id=account_id,
            game_group_id=game_group_id,
        )
        if identity_id in get_identity_ids() and not get_identity_enabled(identity_id):
            set_identity_enabled(identity_id, True)
            initialize_identity_runtime(identity_id, now)
            restored += 1
    remaining_restore_ids = [
        identity_id
        for identity_id in restore_identity_ids
        if identity_id not in restored_identity_ids
    ]
    remaining_frozen_ids = [
        int(identity_id)
        for identity_id in record.get("frozen_identity_ids") or []
        if int(identity_id or 0) not in restored_identity_ids
    ]
    spread_count = spread_overdue_runtime_timers(now, reason="频道身份恢复")
    if spread_count:
        extend_global_recovery_throttle_for_spread(
            now,
            reason="频道身份恢复",
            activate_if_missing=True,
        )
    fully_open = not remaining_restore_ids
    if fully_open:
        _clear_channel_send_as_invalid_observations(account_id, game_group_id)
    set_channel_send_as_health({
        "status": "open" if fully_open else "closed",
        "account_id": account_id,
        "game_group_id": game_group_id,
        "opened_at": now,
        "last_probe_at": now,
        "next_probe_at": 0 if fully_open else now + CHANNEL_SEND_AS_PROBE_INTERVAL_SEC,
        "restore_identity_ids": remaining_restore_ids,
        "frozen_identity_ids": remaining_frozen_ids,
        "last_error": "" if fully_open else "partial_send_as_restore",
    })
    save_state()
    await send_audit_log(
        (
            f"▶️ 游戏群已恢复频道身份发言，自动解冻 {restored} 个频道身份并完成错峰。"
            if fully_open else
            f"▶️ 游戏群部分恢复频道身份发言，本轮解冻 {restored} 个，剩余 {len(remaining_restore_ids)} 个继续复查。"
        ),
        scope="global",
        limit=220,
    )


_PHASEFUL_IDENTITY_SCHEDULERS = (
    run_deep_retreat_scheduler,
    run_yuanying_scheduler,
)
_ORDINARY_IDENTITY_SCHEDULERS = (
    run_pet_scheduler,
    run_ranch_scheduler,
    run_tianxing_scheduler,
    run_wild_training_scheduler,
    run_formation_scheduler,
    run_tianti_scheduler,
    run_quiz_scheduler,
    run_jiyin_scheduler,
    run_concubine_scheduler,
    run_hehuan_scheduler,
    run_nanlong_scheduler,
    run_yinluo_scheduler,
    run_mulan_scheduler,
    run_wanxin_scheduler,
    run_small_world_scheduler,
    run_explore_rift_scheduler,
    run_wendao_scheduler,
    run_duel_scheduler,
    run_fishing_scheduler,
    run_checkin_scheduler,
    run_tower_scheduler,
    run_second_soul_bootstrap_check,
    run_second_soul_scheduler,
    run_taiyi_bootstrap_check,
    run_taiyi_scheduler,
)
_PHASEFUL_BLOCK_CLEANUP_SCHEDULERS = (
    run_concubine_phaseful_cleanup_scheduler,
    run_wanxin_phaseful_cleanup_scheduler,
)
_GLOBAL_SCHEDULERS = (
    ("channel_send_as_health", run_channel_send_as_health_scheduler),
    ("delayed_actions", drain_due_actions),
    ("guanxing_monitor", run_guanxing_monitor_scheduler),
    ("guanxing", run_guanxing_scheduler),
    ("storage_bag_api_keepalive", run_storage_bag_api_keepalive_scheduler),
    ("miniapp_daily", run_miniapp_daily_scheduler),
    ("storage_bag_transfer", run_storage_bag_transfer_scheduler),
    ("divination", run_divination_scheduler),
    ("world_boss", run_world_boss_scheduler),
    ("tiandao_judgement", run_tiandao_judgement_scheduler),
    ("tianji_quiz", run_tianji_quiz_scheduler),
    ("huanglong_conscription", run_huanglong_conscription_scheduler),
    ("luoyun_cd_reminder", run_luoyun_cd_reminder_scheduler),
    ("wanxin_cleanup", run_wanxin_global_cleanup_scheduler),
)

_SCHEDULER_MANIFEST_BRIDGE = {
    "channel_send_as_health": {"manifest_names": (), "helper": True},
    "delayed_actions": {"manifest_names": (), "helper": True},
    "guanxing_monitor": {"manifest_names": ("观星监控",), "helper": False},
    "guanxing": {"manifest_names": ("观星",), "helper": False},
    "storage_bag_api_keepalive": {"manifest_names": ("储物袋",), "helper": True},
    "miniapp_daily": {"manifest_names": ("MiniApp合集", "天机试炼"), "helper": False},
    "storage_bag_transfer": {"manifest_names": ("储物袋",), "helper": False},
    "divination": {"manifest_names": ("卜筮问天",), "helper": False},
    "world_boss": {"manifest_names": ("真仙试锋",), "helper": False},
    "tiandao_judgement": {"manifest_names": (), "helper": True},
    "tianji_quiz": {"manifest_names": (), "helper": True},
    "huanglong_conscription": {"manifest_names": ("自动副本",), "helper": True},
    "luoyun_cd_reminder": {"manifest_names": ("自动副本",), "helper": True},
    "wanxin_cleanup": {"manifest_names": ("婉心封魂",), "helper": True},
    "run_checkin_scheduler": {"manifest_names": ("点卯", "宗门传功"), "helper": False},
    "run_concubine_scheduler": {"manifest_names": ("侍妾", "天机代卜", "共历心劫", "侍妾远航"), "helper": False},
    "run_deep_retreat_scheduler": {"manifest_names": ("深度闭关",), "helper": False},
    "run_formation_scheduler": {"manifest_names": ("周天星斗",), "helper": False},
    "run_hehuan_scheduler": {"manifest_names": ("合欢宗",), "helper": False},
    "run_jiyin_scheduler": {"manifest_names": ("极阴祖师",), "helper": False},
    "run_nanlong_scheduler": {"manifest_names": ("南陇侯",), "helper": False},
    "run_pet_scheduler": {"manifest_names": ("法宝", "温养器灵", "器灵试炼", "布下剑阵"), "helper": False},
    "run_quiz_scheduler": {"manifest_names": ("玄骨考校",), "helper": False},
    "run_ranch_scheduler": {"manifest_names": ("放养",), "helper": False},
    "run_second_soul_bootstrap_check": {"manifest_names": ("第二元神",), "helper": True},
    "run_second_soul_scheduler": {"manifest_names": ("第二元神",), "helper": False},
    "run_small_world_scheduler": {"manifest_names": ("小世界",), "helper": False},
    "run_explore_rift_scheduler": {"manifest_names": ("探寻裂缝",), "helper": False},
    "run_taiyi_bootstrap_check": {"manifest_names": ("太一",), "helper": True},
    "run_taiyi_scheduler": {"manifest_names": ("太一",), "helper": False},
    "run_tianti_scheduler": {"manifest_names": ("登天阶",), "helper": False},
    "run_tianxing_scheduler": {"manifest_names": ("天星宗",), "helper": False},
    "run_tower_scheduler": {"manifest_names": ("闯塔",), "helper": False},
    "run_wendao_scheduler": {"manifest_names": ("问道",), "helper": False},
    "run_mulan_scheduler": {"manifest_names": ("慕兰烽烟",), "helper": False},
    "run_wanxin_scheduler": {"manifest_names": ("婉心封魂",), "helper": False},
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
    "pet_formation": ("剑阵已成", "布下了【大庚剑阵】", "布下剑阵"),
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
    "wanxin_panel": ("婉心封魂", "南宫婉封魂", "月殿余咒", "魂封", "咒源"),
    "wanxin_visit": ("探望南宫婉", "南宫婉", "婉心", "魂封"),
    "wanxin_moon_panel": ("月影同参", "婉影", "南宫婉·月影"),
    "wanxin_moon_greet": ("婉影问安", "情缘", "婉心"),
    "wanxin_moon_seal": ("同参封魂", "情缘", "魂封", "月魄"),
    "wanxin_moon_join": ("月下合参", "封魂咒", "月影"),
    "wanxin_protect": ("护持神魂", "神魂护持", "魂封", "月魄"),
    "wanxin_deduce": ("推演封魂咒", "封魂咒纹", "咒源", "玄冰丹方"),
    "wanxin_commission": ("解咒委托", "委托 ID", "已有进行中的解咒委托"),
    "wanxin_cancel": ("取消解咒委托", "委托已取消", "暂无可取消的解咒委托"),
    "wanxin_accept": ("咒契协定", "已接取", "解咒委托"),
    "wanxin_assist_identify": ("阴罗辨咒", "辨认咒纹", "咒源", "咒师贡献"),
    "wanxin_assist_banner": ("借幡镇魂", "魂封", "月魄", "咒师贡献"),
    "wanxin_assist_strip": ("剥离咒源", "阴罗残咒", "咒源尚未辨明", "咒师贡献"),
    "world_boss": ("真仙试锋", "讨伐青元子", "青元子", "魔压", "阵势", "世界Boss"),
}

OBSERVED_COMMAND_REPLY_HINTS = {
    "万宝楼": ("万宝楼", "搜索结果", "挂单ID", "购买"),
    "世界boss": ("真仙试锋", "世界Boss", "青元子", "魔压", "讨伐"),
    "我的侍妾": ("侍妾", "道侣", "红尘", "情缘", "掩月心契"),
    "第二元神": ("第二元神", "元神", "心魔", "魔染"),
    "野外历练": ("野外历练", "荒野深处", "山中灵机未复", "妖兽", "负伤而归"),
    "闯塔": ("琉璃问心塔", "试炼古塔", "塔相", "总收获", "塔印"),
    "天机试炼": ("天机试炼台", "试炼已绑定", "进入天机试炼台"),
}


def _normalize_command_label(command):
    raw_command = str(command or "").strip()
    if raw_command.startswith("."):
        raw_command = raw_command[1:]
    if not raw_command:
        return ""
    return raw_command.split(maxsplit=1)[0].strip()


def _prune_observed_game_commands(now=None):
    now = float(now if now is not None else time.time())
    expired = [
        msg_id
        for msg_id, item in _observed_game_commands.items()
        if now - float((item or {}).get("at", now) or now) > OBSERVED_GAME_COMMAND_TTL_SEC
    ]
    for msg_id in expired:
        _observed_game_commands.pop(msg_id, None)
    if len(_observed_game_commands) <= OBSERVED_GAME_COMMAND_CAP:
        return
    overflow = len(_observed_game_commands) - OBSERVED_GAME_COMMAND_CAP
    oldest = sorted(
        _observed_game_commands,
        key=lambda msg_id: float((_observed_game_commands.get(msg_id) or {}).get("at", 0) or 0),
    )[:overflow]
    for msg_id in oldest:
        _observed_game_commands.pop(msg_id, None)


def _observe_game_command_for_bot_evidence(sender_id, text, msg_id, *, now=None):
    command = str(text or "").strip()
    if not command.startswith("."):
        return None
    try:
        msg_id = int(msg_id or 0)
    except (TypeError, ValueError):
        msg_id = 0
    if msg_id <= 0:
        return None
    now = float(now if now is not None else time.time())
    _prune_observed_game_commands(now)
    item = {
        "sender_id": int(sender_id or 0),
        "command": command,
        "family": resolve_reply_family(command) or "",
        "command_label": _normalize_command_label(command),
        "at": now,
    }
    _observed_game_commands[msg_id] = item
    return item


def _get_observed_game_command(reply_to_msg_id, *, now=None):
    try:
        reply_to_msg_id = int(reply_to_msg_id or 0)
    except (TypeError, ValueError):
        reply_to_msg_id = 0
    if reply_to_msg_id <= 0:
        return None
    now = float(now if now is not None else time.time())
    _prune_observed_game_commands(now)
    item = _observed_game_commands.get(reply_to_msg_id)
    if not item:
        return None
    if now - float(item.get("at", now) or now) > OBSERVED_GAME_COMMAND_TTL_SEC:
        _observed_game_commands.pop(reply_to_msg_id, None)
        return None
    return item


def _observed_command_reply_matches(text, command_record):
    raw_text = str(text or "").strip()
    if not raw_text or raw_text.startswith(".") or not command_record:
        return False
    family = str(command_record.get("family") or "").strip()
    if family and _looks_like_game_bot_reply(raw_text, family):
        return True
    command_label = str(command_record.get("command_label") or "").strip()
    hints = OBSERVED_COMMAND_REPLY_HINTS.get(command_label) or ()
    if hints and any(hint in raw_text for hint in hints):
        return True
    return bool(command_label and len(command_label) >= 2 and command_label in raw_text)


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


def _refresh_identity_username_from_event(event, identity_sender_id=0):
    identity_sender_id = int(identity_sender_id or _resolve_identity_sender_id(getattr(event, "sender_id", 0)) or 0)
    if identity_sender_id <= 0:
        return False
    sender = getattr(event, "sender", None)
    username = str(
        getattr(sender, "username", "")
        or getattr(event, "sender_username", "")
        or ""
    ).strip().lstrip("@")
    if not username:
        return False
    profile = get_send_as_profile(identity_sender_id)
    current = str(profile.get("username") or "").strip().lstrip("@")
    if current.lower() == username.lower():
        return False
    update_send_as_profile(identity_sender_id, username=username)
    mark_dirty()
    save_state()
    console_log(
        f"🪪 Telegram 用户名已自动刷新：@{current or '未记录'} → @{username}",
        scope="identity",
        send_as_id=identity_sender_id,
        limit=180,
    )
    return True


def _looks_like_game_bot_reply(text, family):
    raw_text = str(text or "").strip()
    if not raw_text or raw_text.startswith("."):
        return False
    hints = BOT_REPLY_FAMILY_HINTS.get(str(family or "").strip()) or ()
    return any(hint in raw_text for hint in hints)


BOT_HEALTH_BROADCAST_MARKERS = (
    "【世界通告｜",
    "✨ 天道感应：",
    "✨ 元神回响：",
    "深度闭关总结",
    "元神归窍总结",
    "【深度闭关总结】",
    "【世界通告",
)


def _looks_like_unanchored_game_broadcast(text):
    raw_text = str(text or "").strip()
    if not raw_text or raw_text.startswith("."):
        return False
    return any(marker in raw_text for marker in BOT_HEALTH_BROADCAST_MARKERS)


def _bot_health_reply_to_msg_id(reply_to):
    """Normalize either a Telegram message object or a raw message id."""
    raw_value = getattr(reply_to, "id", reply_to)
    try:
        return int(raw_value or 0)
    except (TypeError, ValueError):
        return 0


def _is_bot_health_reply_evidence(text, reply_to=None, reply_context=None, *, now=None):
    """Return true only for command-linked bot replies, not passive broadcasts."""
    raw_text = str(text or "").strip()
    if not raw_text or raw_text.startswith("."):
        return False
    if _looks_like_unanchored_game_broadcast(raw_text):
        return False
    reply_to = _bot_health_reply_to_msg_id(reply_to)
    reply_context = reply_context or {}

    routed_identity_id = int(reply_context.get("send_as_id") or 0)
    if routed_identity_id > 0 and reply_to > 0:
        return True

    command_record = _get_observed_game_command(reply_to, now=now)
    if command_record and _observed_command_reply_matches(raw_text, command_record):
        return True

    return False


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
        if normalized_name == HAN_TIANZUN_BOT_NAME or HAN_TIANZUN_BOT_NAME in normalized_name:
            return True
    username = str(getattr(entity, "username", "") or "").strip().lower()
    return bool(username.startswith("hantianzun") and username.endswith("_bot"))


async def _learn_game_bot_id(sender_id, reason, *, evidence=None):
    sender_id = int(sender_id or 0)
    previous_ids = sorted({int(bot_id) for bot_id in get_game_bot_ids()})
    if sender_id <= 0:
        return False
    if sender_id in set(previous_ids):
        _game_bot_candidate_registry.mark_decided(sender_id, learned=True)
        return False
    known_ids = set(previous_ids)
    known_ids.add(sender_id)
    set_game_bot_ids(sorted(known_ids))
    if save_state() is False:
        set_game_bot_ids(previous_ids)
        await send_audit_log(
            f"⚠️ 游戏 Bot 识别未落盘，已回滚内存配置：{sender_id}｜{reason}",
            scope="global",
            limit=260,
        )
        return False
    _game_bot_candidate_registry.mark_decided(sender_id, learned=True)
    if evidence:
        username = str((evidence or {}).get("username") or "").strip()
        commands = [
            str(value).strip()
            for value in ((evidence or {}).get("commands") or [])
            if str(value).strip()
        ]
        command_text = "、".join(commands[:8]) or "未记录"
        reply_count = int((evidence or {}).get("reply_count", 0) or 0)
        player_count = int((evidence or {}).get("player_count", 0) or 0)
        bot_line = f"BOT: @{username}" if username else "BOT: 未知用户名"
        await send_audit_log(
            "\n".join(
                [
                    "🤖 自动识别到游戏 BOT",
                    bot_line,
                    f"ID: {sender_id}",
                    f"证据: {reply_count} 条回复 / {player_count} 个玩家",
                    f"指令: {command_text}",
                    "状态: 已自动加入配置，已持久化",
                ]
            ),
            scope="global",
            limit=520,
        )
    else:
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
    setattr(event, "_xiuxian_sender_is_game_bot", False)
    return False


async def _note_game_bot_activity(text=None, reply_to=None, reply_context=None, *, now=None):
    global _bot_silence_auto_paused
    now = float(now if now is not None else time.time())
    if not _is_bot_health_reply_evidence(text, reply_to, reply_context, now=now):
        return
    reply_to_msg_id = _bot_health_reply_to_msg_id(reply_to)
    bot_health_action = note_game_bot_message(now, reply_to_msg_id=reply_to_msg_id)
    if (
        bot_health_action is None
        and not _bot_silence_auto_paused
        and not get_global_enabled()
        and get_global_pause_source() == "bot_health_monitor"
        and not should_pause_for_bot_health()
    ):
        _bot_silence_auto_paused = True
        restore_bot_health_auto_pause("恢复持久化天尊健康暂停态")
        bot_health_action = note_game_bot_message(now, reply_to_msg_id=reply_to_msg_id)
    if bot_health_action == "probe":
        if _bot_silence_auto_paused or not get_global_enabled():
            _bot_silence_auto_paused = True
            _fire_and_forget(_send_bot_health_probe())
    elif bot_health_action == "recover":
        can_auto_recover = _bot_silence_auto_paused or get_global_pause_source() == "bot_health_monitor"
        if not get_global_enabled() and can_auto_recover:
            await toggle_global_enabled(True, source="bot_health_recovery")
        _bot_silence_auto_paused = False
        mark_bot_health_recovered("bot 恢复确认完成")


def _game_bot_sender_username(event):
    sender = getattr(event, "sender", None)
    username = str(getattr(sender, "username", "") or "").strip()
    if username:
        return username
    return str(getattr(event, "sender_username", "") or "").strip()


def _is_strict_han_tianzun_shard_username(username):
    return bool(re.fullmatch(r"hantianzun\d+_bot", str(username or "").strip(), flags=re.IGNORECASE))


async def _record_suspected_game_bot(
    sender_id,
    family,
    text,
    *,
    verified_bot=False,
    player_id=0,
    command_label="",
    reply_to_msg_id=0,
    sender_username="",
):
    sender_id = int(sender_id or 0)
    now = time.time()
    decision = _game_bot_candidate_registry.observe(
        sender_id,
        now=now,
        family=family,
        player_id=player_id,
        command_label=command_label,
        reply_to_msg_id=reply_to_msg_id,
        known=sender_id in set(get_game_bot_ids()),
    )
    if sender_id == 0 or decision.already_decided:
        return
    item = _suspected_game_bot_hits.get(sender_id) or {}

    if decision.should_notify:
        await send_audit_log(
            f"🧩 检测到未登记游戏 bot 回复候选：{sender_id}｜{family or command_label}｜{str(text or '')[:60]}",
            scope="global",
            limit=260,
        )

    if decision.ready_to_learn:
        if verified_bot:
            evidence = _game_bot_candidate_registry.evidence(sender_id, username=sender_username)
            await _learn_game_bot_id(
                sender_id,
                f"连续命中 {item['count']} 次",
                evidence=evidence,
            )
        else:
            _game_bot_candidate_registry.mark_decided(sender_id, learned=False)
            await send_audit_log(
                f"🧩 未登记游戏 bot 候选命中 {item['count']} 次但 sender 非 bot，未写入 game_bot_ids：{sender_id}",
                scope="global",
                limit=260,
            )


async def _record_external_game_bot_evidence(event, text, now, *, verified_bot=False):
    reply_to_msg_id = _get_event_reply_header_msg_id(event)
    command_record = _get_observed_game_command(reply_to_msg_id, now=now)
    if not command_record:
        return False
    semantic_match = _observed_command_reply_matches(text, command_record)
    sender = getattr(event, "sender", None)
    official_anchor_fallback = bool(
        not semantic_match
        and verified_bot
        and _entity_is_han_tianzun_bot(sender)
        and reply_to_msg_id > 0
        and str(text or "").strip()
        and not str(text or "").strip().startswith(".")
        and not _looks_like_unanchored_game_broadcast(text)
    )
    if not semantic_match and not official_anchor_fallback:
        return False
    await _record_suspected_game_bot(
        int(getattr(event, "sender_id", 0) or 0),
        command_record.get("family") or "",
        text,
        verified_bot=verified_bot,
        player_id=int(command_record.get("sender_id", 0) or 0),
        command_label=command_record.get("command_label") or "",
        reply_to_msg_id=reply_to_msg_id,
        sender_username=_game_bot_sender_username(event),
    )
    return True


async def _handle_suspected_game_bot_reply(event, text, now, *, edited=False):
    sender_id = int(getattr(event, "sender_id", 0) or 0)
    if _resolve_identity_sender_id(sender_id):
        return False
    sender = getattr(event, "sender", None)
    if sender is None:
        try:
            sender = await event.get_sender()
        except Exception:
            sender = None
    sender_is_official_bot = _entity_is_han_tianzun_bot(sender)
    if not sender_is_official_bot:
        return False
    sender_username = _game_bot_sender_username(event)
    recorded_exact_reply = await _record_external_game_bot_evidence(event, text, now, verified_bot=True)
    if recorded_exact_reply and _is_strict_han_tianzun_shard_username(sender_username):
        await _learn_game_bot_id(
            sender_id,
            "官方分片精确回复我方已登记命令",
            evidence=_game_bot_candidate_registry.evidence(sender_id, username=sender_username),
        )
    try:
        reply_to, reply_context = await _resolve_event_reply(event)
    except Exception:
        return False
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
    now = time.time()
    for identity_id in get_identity_ids():
        if (
            get_identity_enabled(identity_id)
            and not _is_identity_account_offline(identity_id)
            and not is_identity_weak(identity_id, now)
        ):
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
        return
    note_bot_health_probe_attempt(time.time())
    await send_audit_log("🩺 天尊恢复探测未发出，已记录探测尝试并等待超时回退/真实回复恢复。", scope="identity", send_as_id=identity_id, limit=220)


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


def _get_event_listener_account_id(event):
    event_client = getattr(event, "client", None)
    for account_id, account_client in get_all_clients().items():
        if event_client is account_client:
            try:
                return int(account_id or 0)
            except (TypeError, ValueError):
                return 0
    if event_client is client:
        try:
            return int(state.get("my_user_id") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _is_game_group_listener_event(event):
    try:
        chat_id = int(getattr(event, "chat_id", 0) or 0)
    except (TypeError, ValueError):
        chat_id = 0
    if chat_id != int(get_game_group_id() or 0):
        return True
    listener_ids = set(get_game_listener_account_ids())
    if not listener_ids:
        return True
    return _get_event_listener_account_id(event) in listener_ids


async def _resolve_event_reply(event):
    try:
        reply_to = await event.get_reply_message()
    except Exception as exc:
        if "Cannot send requests while disconnected" in str(exc):
            reply_to = None
        elif is_account_session_error(exc):
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


def _prune_early_routed_replies(now=None):
    now = float(now if now is not None else time.time())
    expired = [
        root_msg_id
        for root_msg_id, items in _early_routed_replies.items()
        if not items or max(float(item.get("remembered_at", 0) or 0) for item in items) + _EARLY_ROUTED_REPLY_TTL_SEC <= now
    ]
    for root_msg_id in expired:
        _early_routed_replies.pop(root_msg_id, None)
    if len(_early_routed_replies) <= _EARLY_ROUTED_REPLY_MAX:
        return
    overflow = len(_early_routed_replies) - _EARLY_ROUTED_REPLY_MAX
    oldest = sorted(
        _early_routed_replies,
        key=lambda root_msg_id: max(
            float(item.get("remembered_at", 0) or 0)
            for item in (_early_routed_replies.get(root_msg_id) or [{}])
        ),
    )[:overflow]
    for root_msg_id in oldest:
        _early_routed_replies.pop(root_msg_id, None)


def _remember_early_routed_reply(event, text, now, reply_to, reply_context, *, event_kind):
    context = reply_context if isinstance(reply_context, dict) else {}
    if str(context.get("matched_via") or "") != "reply_sender":
        return False
    root_msg_id = int(context.get("reply_to_msg_id") or getattr(reply_to, "id", 0) or 0)
    identity_id = int(context.get("send_as_id") or 0)
    if root_msg_id <= 0 or identity_id <= 0:
        return False
    _prune_early_routed_replies(now)
    items = _early_routed_replies.setdefault(root_msg_id, [])
    event_id = int(getattr(event, "id", 0) or 0)
    normalized_kind = "edit" if str(event_kind or "").strip().lower() == "edit" else "message"
    if any(int(item.get("event_id", 0) or 0) == event_id and item.get("event_kind") == normalized_kind for item in items):
        return True
    items.append({
        "event": event,
        "event_id": event_id,
        "event_kind": normalized_kind,
        "text": str(text or ""),
        "event_at": float(now or time.time()),
        "reply_to": reply_to,
        "remembered_at": time.time(),
    })
    return True


def _logged_reply_event(entry, command, send_as_id):
    root_msg_id = int((entry or {}).get("reply_to_msg_id") or 0)
    topic_id = int((entry or {}).get("topic_id") or 0)
    reply_header = SimpleNamespace(reply_to_msg_id=root_msg_id, reply_to_top_id=topic_id)
    event = SimpleNamespace(
        id=int((entry or {}).get("message_id") or 0),
        chat_id=int((entry or {}).get("chat_id") or get_game_group_id() or 0),
        sender_id=int((entry or {}).get("sender_id") or 0),
        raw_text=str((entry or {}).get("text") or ""),
        reply_to=reply_header,
        message=SimpleNamespace(buttons=None),
    )
    reply_to = SimpleNamespace(id=root_msg_id, raw_text=str(command or ""), sender_id=int(send_as_id or 0))
    return event, reply_to


def _is_logged_game_bot_reply(entry):
    if not isinstance(entry, dict):
        return False
    text = str(entry.get("text") or "").strip()
    if not text or text.startswith("."):
        return False
    try:
        sender_id = int(entry.get("sender_id") or 0)
    except (TypeError, ValueError, OverflowError):
        sender_id = 0
    if sender_id > 0 and sender_id in {int(bot_id) for bot_id in get_game_bot_ids()}:
        return True
    return entry.get("sender_is_bot") is True


async def _replay_early_replies_after_sent(send_as_id, command, sent_at, msg_id, *, allow_log_fallback=False):
    await asyncio.sleep(_EARLY_ROUTED_REPLY_REPLAY_DELAY_SEC)
    _prune_early_routed_replies()
    items = list(_early_routed_replies.pop(int(msg_id or 0), []))
    if not items and allow_log_fallback:
        logged = find_message_log_replies_tail(
            msg_id,
            time.time(),
            lookback_sec=max(30, int(time.time() - float(sent_at or time.time())) + 30),
            lookahead_sec=5,
            predicate=_is_logged_game_bot_reply,
        )
        for entry in logged:
            event, reply_to = _logged_reply_event(entry, command, send_as_id)
            items.append({
                "event": event,
                "event_id": int(getattr(event, "id", 0) or 0),
                "event_kind": str((entry or {}).get("event_type") or "message"),
                "text": str((entry or {}).get("text") or ""),
                "event_at": float((entry or {}).get("ts_epoch") or time.time()),
                "reply_to": reply_to,
                "remembered_at": time.time(),
            })
    if not items:
        return False

    family = resolve_reply_family(command) or ""
    replayed = False
    for item in sorted(items, key=lambda value: (float(value.get("event_at", 0) or 0), int(value.get("event_id", 0) or 0))):
        event = item.get("event")
        reply_to = item.get("reply_to") or SimpleNamespace(id=int(msg_id or 0), raw_text=str(command or ""))
        if event is None:
            continue
        context = get_reply_context(reply_to, reply_to_msg_id=msg_id, send_as_id=send_as_id)
        context.update({
            "send_as_id": int(send_as_id or 0),
            "family": family or context.get("family"),
            "reply_to_msg_id": int(msg_id or 0),
            "root_msg_id": int(msg_id or 0),
            "matched_via": "late_sent_replay",
        })
        event_kind = "edit" if str(item.get("event_kind") or "").strip().lower() == "edit" else "message"
        _bind_command_attempt_shadow(
            event,
            item.get("text") or "",
            float(item.get("event_at", 0) or time.time()),
            context,
            event_kind=event_kind,
        )
        handled = await _handle_routed_reply_event(
            event,
            item.get("text") or "",
            float(item.get("event_at", 0) or time.time()),
            reply_to,
            context,
            event_kind=event_kind,
            replay=True,
        )
        replayed = bool(handled) or replayed
    if replayed:
        console_log(
            f"♻️ 已在发送登记后重放早到回复：{str(command or '')[:32]}｜cmd_msg={int(msg_id or 0)}",
            scope="identity",
            send_as_id=send_as_id,
            limit=180,
        )
    return replayed


def _observe_sent_for_early_reply_replay(send_as_id, command, *, now, msg_id, **metadata):
    if int(msg_id or 0) <= 0:
        return
    has_cached_reply = int(msg_id or 0) in _early_routed_replies
    allow_log_fallback = bool(metadata.get("recovered")) or float(metadata.get("send_elapsed_sec", 0) or 0) >= 1.0
    if not has_cached_reply and not allow_log_fallback:
        return
    _fire_and_forget(
        _replay_early_replies_after_sent(
            send_as_id,
            command,
            now,
            msg_id,
            allow_log_fallback=allow_log_fallback,
        )
    )


def _bind_command_attempt_shadow(event, text, now, reply_context, *, event_kind):
    context = reply_context if isinstance(reply_context, dict) else {}
    return bind_shadow_evidence(
        event_kind=event_kind,
        msg_id=int(getattr(event, "id", 0) or 0),
        reply_to_msg_id=int(context.get("reply_to_msg_id") or 0),
        identity_id=int(context.get("send_as_id") or 0),
        family=str(context.get("family") or ""),
        text=text,
        op_id=str(context.get("op_id") or ""),
        chain_id=str(context.get("chain_id") or ""),
        event_at=now,
        source="live",
        payload={"matched_via": str(context.get("matched_via") or "")},
    )


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


async def _handle_trial_miniapp_broadcast_entry(text, now, event):
    return await handle_trial_miniapp_entry(
        event,
        text,
        now,
        reply_to=None,
        matched_family=None,
        result_msg_id=getattr(event, "id", 0),
        require_identity_match=True,
    )


async def _handle_cave_treasure_miniapp_broadcast_entry(text, now, event):
    return await handle_cave_treasure_miniapp_entry(
        event,
        text,
        now,
        reply_to=None,
        matched_family=None,
        result_msg_id=getattr(event, "id", 0),
        require_identity_match=True,
    )


async def _handle_tree_miniapp_broadcast_entry(text, now, event):
    return await handle_tree_miniapp_entry(
        event,
        text,
        now,
        reply_to=None,
        matched_family=None,
        result_msg_id=getattr(event, "id", 0),
        require_identity_match=True,
    )


async def _dispatch_miniapp_broadcast_fallbacks(event, text, now):
    raw_text = str(text or "")
    if "天机试炼台" in raw_text and _claim_runtime_event(event, scope="trial_miniapp_orphan_entry"):
        if await _run_until_handled_for_enabled_identities(_handle_trial_miniapp_broadcast_entry, raw_text, now, event):
            return True
    if "洞府" in raw_text and _claim_runtime_event(event, scope="cave_treasure_miniapp_orphan_entry"):
        if await _run_until_handled_for_enabled_identities(_handle_cave_treasure_miniapp_broadcast_entry, raw_text, now, event):
            return True
    if ("灵眼之树" in raw_text or "进入灵树" in raw_text) and _claim_runtime_event(event, scope="tree_miniapp_orphan_entry"):
        if await _handle_tree_miniapp_broadcast_entry(raw_text, now, event):
            return True
    return False


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
    if _claim_runtime_event(event, scope="duel_target_observation"):
        await _run_until_handled_for_enabled_identities(handle_duel_target_observation, text, now, event)
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
            enforce_identity_module_availability(identity_id)
            for scheduler in _PHASEFUL_IDENTITY_SCHEDULERS:
                await scheduler(time.time())
            if has_phaseful_summary_block(time.time()):
                for scheduler in _PHASEFUL_BLOCK_CLEANUP_SCHEDULERS:
                    await scheduler(time.time())
                continue
            for scheduler in _ORDINARY_IDENTITY_SCHEDULERS:
                await scheduler(time.time())


async def _run_due_wild_training_retry_schedulers(now, *, limit=DUE_WILD_TRAINING_MAX_PER_TICK):
    global _due_wild_training_last_diag_at
    candidates = []
    for scan_index, identity_id in enumerate(get_identity_ids()):
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
            if not state.get("wild_training_enabled"):
                continue
            reconcile_wild_training_daily_reset_spread(scheduler_now)
            try:
                next_time = float(state.get("next_wild_training_time", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                next_time = 0.0
            explore_released = bool(is_tianxing_route_released("探索", now=scheduler_now, require_change_fate=True))
            if explore_released and next_time > scheduler_now:
                last_result = str(state.get("wild_training_last_result") or "")
                last_error = str(state.get("wild_training_last_error") or "")
                cooldown_due_at = _wild_training_completed_cooldown_due_at()
                if cooldown_due_at > scheduler_now:
                    if next_time < cooldown_due_at:
                        state["next_wild_training_time"] = cooldown_due_at
                        next_time = cooldown_due_at
                        mark_dirty()
                else:
                    release_retry_until = scheduler_now + WILD_TRAINING_RETRY_MAX_SEC + 5
                    if next_time <= release_retry_until and ("天星时间线" in last_result or "天星时间线" in last_error):
                        state["next_wild_training_time"] = scheduler_now
                        state["wild_training_last_error"] = "天星探索已放行，恢复错峰计时已压回立即消费窗口"
                        next_time = scheduler_now
                        mark_dirty()
            if next_time <= 0 or next_time > scheduler_now:
                if next_time > scheduler_now:
                    with use_identity(identity_id):
                        windows = build_tianxing_consume_window(
                            "探索",
                            now=scheduler_now,
                            due_at=next_time,
                            reason="野外历练",
                            require_change_fate=True,
                        )
                        if (
                            windows
                            and state.get("tianxing_enabled")
                            and not _tianxing_prepare_retry_blocks(scheduler_now)
                        ):
                            candidates.append((0, next_time, scan_index, identity_id, scheduler_now))
                            continue
                continue
            priority = 1 if state.get("tianxing_enabled") else 2
            candidates.append((priority, next_time, scan_index, identity_id, scheduler_now))

    if candidates and float(now or 0) - _due_wild_training_last_diag_at >= DUE_WILD_TRAINING_DIAG_INTERVAL_SEC:
        _due_wild_training_last_diag_at = float(now or time.time())
        preview = []
        for _priority, due_at, _scan_index, identity_id, _scheduler_now in sorted(candidates)[:5]:
            profile = get_send_as_profile(identity_id)
            username = str((profile or {}).get("username") or identity_id)
            overdue = max(0, int(float(now or time.time()) - float(due_at or 0)))
            preview.append(f"@{username}:run/{overdue}s")
        console_log(
            f"🏞️ 到期野外扫描候选 {len(candidates)} 个，本轮上限 {int(limit or 1)}：{', '.join(preview)}",
            scope="global",
        )

    processed = 0
    for _priority, _due_at, _scan_index, identity_id, scheduler_now in sorted(candidates):
        if processed >= int(limit or 1):
            break
        try:
            await asyncio.wait_for(
                _run_due_wild_training_candidate(identity_id, scheduler_now),
                timeout=max(1, float(DUE_WILD_TRAINING_SCHEDULER_TIMEOUT_SEC or 0)),
            )
        except asyncio.TimeoutError:
            with use_identity(identity_id):
                _record_due_wild_training_candidate_failure(
                    now=time.time(),
                    reason=f"到期野外扫描执行超时（>{int(DUE_WILD_TRAINING_SCHEDULER_TIMEOUT_SEC)}s），已让出本轮避免阻塞其他身份",
                )
            profile = get_send_as_profile(identity_id)
            username = str((profile or {}).get("username") or identity_id)
            console_log(f"🏞️ 到期野外扫描超时：@{username}", scope="global")
        except Exception as exc:
            with use_identity(identity_id):
                _record_due_wild_training_candidate_failure(
                    now=time.time(),
                    reason=f"到期野外扫描异常：{str(exc)[:160]}",
                )
            print("due wild training scheduler failed:")
            print("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        processed += 1


def _wild_training_completed_cooldown_due_at():
    try:
        return max(0.0, float(state.get("next_wild_training_time", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        return 0.0


async def _run_due_wild_training_candidate(identity_id, scheduler_now):
    with use_identity(identity_id):
        candidate_now = max(float(scheduler_now or 0), time.time())
        await run_wild_training_scheduler(candidate_now)


def _record_due_wild_training_candidate_failure(*, now, reason):
    state["wild_training_last_error"] = str(reason or "到期野外扫描失败")
    state["wild_training_last_result_at"] = 0
    state["next_wild_training_time"] = float(now) + WILD_TRAINING_RETRY_MIN_SEC
    mark_dirty()


async def _run_due_explore_rift_schedulers(now, *, limit=DUE_EXPLORE_RIFT_MAX_PER_TICK):
    global _due_explore_rift_last_diag_at
    candidates = []
    for scan_index, identity_id in enumerate(get_identity_ids()):
        if not get_identity_enabled(identity_id):
            continue
        if _is_identity_account_offline(identity_id):
            continue
        with use_identity(identity_id):
            scheduler_now = max(float(now or 0), time.time())
            if is_identity_weak(identity_id, scheduler_now):
                continue
            if has_phaseful_summary_block(scheduler_now):
                continue
            if not state.get("explore_rift_enabled"):
                continue
            pending_msg_id = int(state.get("explore_rift_reply_to_msg_id", 0) or 0)
            pending_result_msg_id = int(state.get("explore_rift_pending_result_msg_id", 0) or 0)
            try:
                reply_due_at = float(state.get("explore_rift_reply_due_at", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                reply_due_at = 0.0
            if pending_result_msg_id > 0 and reply_due_at <= 0:
                candidates.append((0, scheduler_now, scan_index, identity_id, scheduler_now, "cleanup"))
                continue
            if (pending_msg_id > 0 or pending_result_msg_id > 0) and 0 < reply_due_at <= scheduler_now:
                candidates.append((0, reply_due_at, scan_index, identity_id, scheduler_now, "cleanup"))
                continue
            if pending_msg_id > 0 or pending_result_msg_id > 0:
                continue
            try:
                next_time = float(state.get("next_explore_rift_time", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                next_time = 0.0
            if next_time <= 0:
                continue
            if next_time <= scheduler_now:
                candidates.append((1, next_time, scan_index, identity_id, scheduler_now, "run"))
                continue
            if not state.get("tianxing_enabled"):
                continue
            try:
                prepare_retry_at = float(state.get("explore_rift_tianxing_prepare_retry_at", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                prepare_retry_at = 0.0
            if prepare_retry_at > scheduler_now:
                continue
            windows = build_tianxing_consume_window(
                "探索",
                now=scheduler_now,
                due_at=next_time,
                reason="探寻裂缝",
                require_change_fate=True,
            )
            if windows:
                candidates.append((2, next_time, scan_index, identity_id, scheduler_now, "prepare"))

    if candidates and float(now or 0) - _due_explore_rift_last_diag_at >= DUE_EXPLORE_RIFT_DIAG_INTERVAL_SEC:
        _due_explore_rift_last_diag_at = float(now or time.time())
        preview = []
        for _priority, due_at, _scan_index, identity_id, _scheduler_now, action in sorted(candidates)[:5]:
            profile = get_send_as_profile(identity_id)
            username = str((profile or {}).get("username") or identity_id)
            overdue = max(0, int(float(now or time.time()) - float(due_at or 0)))
            preview.append(f"@{username}:{action}/{overdue}s")
        console_log(
            f"🕳 到期探缝扫描候选 {len(candidates)} 个，本轮上限 {int(limit or 1)}：{', '.join(preview)}",
            scope="global",
        )

    processed = 0
    for _priority, _due_at, _scan_index, identity_id, scheduler_now, action in sorted(candidates):
        if processed >= int(limit or 1):
            break
        try:
            await asyncio.wait_for(
                _run_due_explore_rift_candidate(identity_id, scheduler_now),
                timeout=max(1, float(DUE_EXPLORE_RIFT_SCHEDULER_TIMEOUT_SEC or 0)),
            )
        except asyncio.TimeoutError:
            with use_identity(identity_id):
                _record_due_explore_rift_candidate_failure(
                    action,
                    now=time.time(),
                    reason=f"到期探缝扫描执行超时（>{int(DUE_EXPLORE_RIFT_SCHEDULER_TIMEOUT_SEC)}s），已让出本轮避免阻塞其他身份",
                )
            profile = get_send_as_profile(identity_id)
            username = str((profile or {}).get("username") or identity_id)
            console_log(f"🕳 到期探缝扫描超时：@{username} {action}", scope="global")
        except Exception as exc:
            with use_identity(identity_id):
                _record_due_explore_rift_candidate_failure(
                    action,
                    now=time.time(),
                    reason=f"到期探缝扫描异常：{str(exc)[:160]}",
                )
            print("due explore rift scheduler failed:")
            print("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        processed += 1


async def _run_due_explore_rift_candidate(identity_id, scheduler_now):
    with use_identity(identity_id):
        candidate_now = max(float(scheduler_now or 0), time.time())
        await run_explore_rift_scheduler(candidate_now)


def _record_due_explore_rift_candidate_failure(action, *, now, reason):
    state["explore_rift_last_error"] = str(reason or "到期探缝扫描失败")
    if action == "cleanup":
        if int(state.get("explore_rift_reply_to_msg_id", 0) or 0) > 0:
            state["explore_rift_reply_due_at"] = float(now) + 120
    else:
        state["next_explore_rift_time"] = float(now) + 120
    mark_dirty()


async def _run_due_concubine_schedulers(now, *, limit=DUE_CONCUBINE_MAX_PER_TICK):
    global _due_concubine_last_diag_at
    candidates = []
    for scan_index, identity_id in enumerate(get_identity_ids()):
        if not get_identity_enabled(identity_id):
            continue
        if _is_identity_account_offline(identity_id):
            continue
        with use_identity(identity_id):
            scheduler_now = max(float(now or 0), time.time())
            if is_identity_weak(identity_id, scheduler_now):
                continue
            if has_phaseful_summary_block(scheduler_now):
                continue
            if not any(
                bool(state.get(key))
                for key in (
                    "concubine_enabled",
                    "concubine_tianji_enabled",
                    "concubine_heart_enabled",
                    "concubine_voyage_enabled",
                )
            ):
                continue
            try:
                next_time = float(state.get("next_concubine_time", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                next_time = 0.0
            _clear_due_concubine_transient_error_if_stable(scheduler_now, next_time)
            if next_time <= 0 or next_time > scheduler_now:
                continue
            candidates.append((next_time, scan_index, identity_id, scheduler_now))

    if candidates and float(now or 0) - _due_concubine_last_diag_at >= DUE_CONCUBINE_DIAG_INTERVAL_SEC:
        _due_concubine_last_diag_at = float(now or time.time())
        preview = []
        for due_at, _scan_index, identity_id, _scheduler_now in sorted(candidates)[:5]:
            profile = get_send_as_profile(identity_id)
            username = str((profile or {}).get("username") or identity_id)
            overdue = max(0, int(float(now or time.time()) - float(due_at or 0)))
            preview.append(f"@{username}:run/{overdue}s")
        console_log(
            f"🌸 到期侍妾扫描候选 {len(candidates)} 个，本轮上限 {int(limit or 1)}：{', '.join(preview)}",
            scope="global",
        )

    processed = 0
    for _due_at, _scan_index, identity_id, scheduler_now in sorted(candidates):
        if processed >= int(limit or 1):
            break
        try:
            await asyncio.wait_for(
                _run_due_concubine_candidate(identity_id, scheduler_now),
                timeout=max(1, float(DUE_CONCUBINE_SCHEDULER_TIMEOUT_SEC or 0)),
            )
        except asyncio.TimeoutError:
            with use_identity(identity_id):
                _record_due_concubine_candidate_failure(
                    now=time.time(),
                    reason=f"到期侍妾扫描执行超时（>{int(DUE_CONCUBINE_SCHEDULER_TIMEOUT_SEC)}s），已让出本轮避免阻塞其他身份",
                    transient=True,
                )
            profile = get_send_as_profile(identity_id)
            username = str((profile or {}).get("username") or identity_id)
            console_log(f"🌸 到期侍妾扫描超时：@{username}", scope="global")
        except Exception as exc:
            with use_identity(identity_id):
                _record_due_concubine_candidate_failure(
                    now=time.time(),
                    reason=f"到期侍妾扫描异常：{str(exc)[:160]}",
                )
            print("due concubine scheduler failed:")
            print("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        processed += 1


async def _run_due_concubine_candidate(identity_id, scheduler_now):
    with use_identity(identity_id):
        candidate_now = max(float(scheduler_now or 0), time.time())
        with concubine_send_queue_timeout(CONCUBINE_DUE_SCAN_SEND_QUEUE_TIMEOUT_SEC):
            await run_concubine_scheduler(candidate_now)


def _record_due_concubine_candidate_failure(*, now, reason, transient=False):
    if transient:
        state["concubine_last_result"] = str(reason or "到期侍妾扫描让出本轮")
        state["concubine_last_error"] = ""
    else:
        state["concubine_last_error"] = str(reason or "到期侍妾扫描失败")
    state["next_concubine_time"] = float(now) + WILD_TRAINING_RETRY_MIN_SEC
    mark_dirty()


def _clear_due_concubine_transient_error_if_stable(now, next_time):
    last_error = str(state.get("concubine_last_error") or "")
    if not last_error.startswith("到期侍妾扫描执行超时"):
        return False
    if int(state.get("concubine_reply_to_msg_id", 0) or 0) > 0:
        return False
    try:
        next_time = float(next_time or 0)
    except (TypeError, ValueError, OverflowError):
        next_time = 0.0
    if next_time <= float(now or 0):
        return False
    state["concubine_last_result"] = last_error
    state["concubine_last_error"] = ""
    mark_dirty()
    return True


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


async def _run_tianxing_daily_bootstrap_identity_schedulers(now, *, limit=TIANXING_DAILY_BOOTSTRAP_MAX_PER_TICK):
    processed = 0
    for identity_id in get_identity_ids():
        if processed >= int(limit or 1):
            break
        if not get_identity_enabled(identity_id):
            continue
        if _is_identity_account_offline(identity_id):
            continue
        with use_identity(identity_id):
            scheduler_now = max(float(now or 0), time.time())
            if is_identity_weak(identity_id, scheduler_now):
                continue
            if has_phaseful_summary_block(scheduler_now):
                continue
            result = await run_tianxing_daily_bootstrap_scheduler(scheduler_now)
            if (result or {}).get("active") and (result or {}).get("command"):
                processed += 1


async def _run_tianxing_timeline_followup_identity_schedulers(now, *, limit=TIANXING_TIMELINE_FOLLOWUP_MAX_PER_TICK):
    candidates = []
    for scan_index, identity_id in enumerate(get_identity_ids()):
        if not get_identity_enabled(identity_id):
            continue
        if _is_identity_account_offline(identity_id):
            continue
        with use_identity(identity_id):
            scheduler_now = max(float(now or 0), time.time())
            if is_identity_weak(identity_id, scheduler_now):
                continue
            if has_phaseful_summary_block(scheduler_now):
                continue
            if not has_tianxing_timeline_due_work(scheduler_now):
                continue
            candidates.append((scan_index, identity_id, scheduler_now))

    processed = 0
    for _scan_index, identity_id, scheduler_now in candidates:
        if processed >= int(limit or 1):
            break
        with use_identity(identity_id):
            result = await run_tianxing_timeline_followup_scheduler(scheduler_now)
            if (result or {}).get("active"):
                processed += 1


def _tianxing_downstream_prepare_windows(now):
    now = float(now or 0)
    windows = []
    specs = (
        (
            "wild_training_enabled",
            "next_wild_training_time",
            "wild_training_tianxing_prepare_retry_at",
            (),
            "",
            "野外历练",
        ),
        (
            "explore_rift_enabled",
            "next_explore_rift_time",
            "explore_rift_tianxing_prepare_retry_at",
            ("explore_rift_reply_to_msg_id", "explore_rift_pending_result_msg_id"),
            "explore_rift_reply_due_at",
            "探寻裂缝",
        ),
    )
    for enabled_key, next_key, retry_key, pending_keys, reply_due_key, reason in specs:
        if not state.get(enabled_key):
            continue
        try:
            due_at = float(state.get(next_key, 0) or 0)
            retry_at = float(state.get(retry_key, 0) or 0)
            reply_due_at = float(state.get(reply_due_key, 0) or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if due_at <= 0 or retry_at > now:
            continue
        if any(int(state.get(key, 0) or 0) > 0 for key in pending_keys):
            continue
        if reply_due_at > now:
            continue
        preflight = build_tianxing_route_preflight_plan(
            "探索",
            reason=reason,
            deadline_at=due_at,
            now=now,
            require_change_fate=True,
        )
        if preflight.get("route_allowed"):
            continue
        blocked_until = float(preflight.get("blocked_until", 0) or 0)
        if blocked_until > now:
            continue
        windows.extend(build_tianxing_consume_window(
            "探索",
            now=now,
            due_at=max(due_at, now),
            reason=reason,
            require_change_fate=True,
        ))
    return windows


def _tianxing_fast_due_info(now):
    due_times = []
    observed = normalize_tianxing_observation(state.get("tianxing_observation"))
    try:
        tianji_value = int(observed.get("tianji_value", 999999))
    except (TypeError, ValueError, OverflowError):
        tianji_value = 999999
    try:
        auto_next_time = float(observed.get("auto_next_time", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        auto_next_time = 0.0
    auto_due = auto_next_time > 0 and auto_next_time <= now
    if auto_next_time > 0 and auto_due:
        due_times.append(auto_next_time)
    timeline = normalize_tianxing_timeline_state(state.get("tianxing_timeline_state"))
    craft_farm = timeline.get("craft_farm") if isinstance(timeline.get("craft_farm"), dict) else {}
    try:
        craft_next_time = float(craft_farm.get("next_time", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        craft_next_time = 0.0
    craft_phase = str(craft_farm.get("phase") or "").strip()
    craft_recovery_due = craft_next_time > 0 and craft_next_time <= now and craft_phase in {
        "send_blocked",
        "calibrating",
        "sent_waiting_reply",
        "crafting_waiting_final",
    }
    craft_due = has_tianxing_craft_farm_due(now)
    if craft_recovery_due or craft_due:
        due_times.append(craft_next_time if craft_next_time > 0 else float(now))
    timeline_due = has_tianxing_timeline_due_work(now)
    craft_override_due = has_tianxing_craft_farm_override_due(now)
    downstream_prepare_due = bool(_tianxing_downstream_prepare_windows(now))
    if timeline_due:
        due_times.append(float(now))
    if craft_override_due:
        due_times.append(float(now))
    if downstream_prepare_due:
        due_times.append(float(now))
    due_times = [value for value in due_times if value > 0]
    if not due_times:
        return {"due_at": 0.0, "priority": 99, "tianji": tianji_value}
    if timeline_due or craft_recovery_due or downstream_prepare_due:
        priority = 0
    elif craft_override_due:
        priority = 1
    elif craft_due:
        priority = 2
    elif auto_due:
        priority = 3
    else:
        priority = 9
    return {
        "due_at": min(due_times),
        "priority": priority,
        "tianji": tianji_value,
        "downstream_prepare_due": downstream_prepare_due,
    }


def _tianxing_fast_due_time(now):
    return float(_tianxing_fast_due_info(now).get("due_at", 0.0) or 0.0)


async def _run_due_tianxing_schedulers(now, *, limit=DUE_TIANXING_MAX_PER_TICK):
    global _due_tianxing_last_diag_at
    candidates = []
    for scan_index, identity_id in enumerate(get_identity_ids()):
        if not get_identity_enabled(identity_id):
            continue
        if _is_identity_account_offline(identity_id):
            continue
        with use_identity(identity_id):
            scheduler_now = max(float(now or 0), time.time())
            if is_identity_weak(identity_id, scheduler_now):
                continue
            if not state.get("tianxing_enabled"):
                continue
            downstream_prepare_windows = _tianxing_downstream_prepare_windows(scheduler_now)
            if has_phaseful_summary_block(scheduler_now) and not downstream_prepare_windows:
                continue
            due_info = _tianxing_fast_due_info(scheduler_now)
            due_at = float(due_info.get("due_at", 0.0) or 0.0)
            if due_at <= 0 or due_at > scheduler_now:
                continue
            try:
                priority = int(due_info.get("priority", 99))
            except (TypeError, ValueError, OverflowError):
                priority = 99
            try:
                tianji_value = int(due_info.get("tianji", 999999))
            except (TypeError, ValueError, OverflowError):
                tianji_value = 999999
            candidates.append((
                priority,
                tianji_value,
                due_at,
                scan_index,
                identity_id,
                scheduler_now,
            ))

    if candidates and float(now or 0) - _due_tianxing_last_diag_at >= DUE_TIANXING_DIAG_INTERVAL_SEC:
        _due_tianxing_last_diag_at = float(now or time.time())
        preview = []
        for priority, tianji_value, due_at, _scan_index, identity_id, _scheduler_now in sorted(candidates)[:5]:
            profile = get_send_as_profile(identity_id)
            username = str((profile or {}).get("username") or identity_id)
            overdue = max(0, int(float(now or time.time()) - float(due_at or 0)))
            preview.append(f"@{username}:p{priority}/tj{tianji_value}/run/{overdue}s")
        console_log(
            f"🌌 到期天星扫描候选 {len(candidates)} 个，本轮上限 {int(limit or 1)}：{', '.join(preview)}",
            scope="global",
        )

    processed = 0
    for _priority, _tianji_value, _due_at, _scan_index, identity_id, scheduler_now in sorted(candidates):
        if processed >= int(limit or 1):
            break
        try:
            await asyncio.wait_for(
                _run_due_tianxing_candidate(identity_id, scheduler_now),
                timeout=max(1, float(DUE_TIANXING_SCHEDULER_TIMEOUT_SEC or 0)),
            )
        except asyncio.TimeoutError:
            with use_identity(identity_id):
                observed = normalize_tianxing_observation(state.get("tianxing_observation"))
                observed["auto_last_action"] = "fast_due"
                observed["auto_last_plan"] = "timeout_yield"
                observed["auto_last_plan_at"] = float(time.time())
                observed["auto_next_time"] = float(time.time() + 60)
                state["tianxing_observation"] = observed
                mark_dirty()
            profile = get_send_as_profile(identity_id)
            username = str((profile or {}).get("username") or identity_id)
            console_log(f"🌌 到期天星扫描超时：@{username}", scope="global")
        except Exception as exc:
            with use_identity(identity_id):
                observed = normalize_tianxing_observation(state.get("tianxing_observation"))
                observed["auto_last_action"] = "fast_due"
                observed["auto_last_error"] = f"到期天星扫描异常：{str(exc)[:160]}"
                observed["auto_last_error_at"] = float(time.time())
                observed["auto_next_time"] = float(time.time() + 120)
                state["tianxing_observation"] = observed
                mark_dirty()
            print("due tianxing scheduler failed:")
            print("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        processed += 1


async def _run_due_tianxing_candidate(identity_id, scheduler_now):
    with use_identity(identity_id):
        candidate_now = max(float(scheduler_now or 0), time.time())
        windows = _tianxing_downstream_prepare_windows(candidate_now)
        if windows:
            await run_tianxing_timeline_scheduler(candidate_now, windows=windows)
        await run_tianxing_scheduler(candidate_now)


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


async def _handle_routed_reply_event(
    event,
    text,
    now,
    reply_to,
    reply_context,
    *,
    allow_tree_panel_claim=True,
    event_kind="message",
    replay=False,
):
    routed_identity_id = int((reply_context or {}).get("send_as_id") or 0)
    matched_family = (reply_context or {}).get("family") or None
    if routed_identity_id <= 0:
        return False

    if not replay:
        _remember_early_routed_reply(event, text, now, reply_to, reply_context, event_kind=event_kind)

    family_scope = str(matched_family or "unknown").strip() or "unknown"
    kind_scope = str(event_kind or "message").strip() or "message"
    edit_text_scope = f":{hash(str(text or ''))}" if kind_scope == "edit" else ""
    claim_prefix = "routed_reply_replay" if replay else "routed_reply"
    if not _claim_runtime_event(event, scope=f"{claim_prefix}:{kind_scope}:{routed_identity_id}:{family_scope}{edit_text_scope}"):
        return False

    # In multi-client mode a bot reply can be delivered to a different account
    # client than the one that sent the command. The reply_to message id is the
    # authoritative owner here; requiring the owner client would leave pending
    # tasks uncleared and trigger retry storms.
    allow_reprocessed_edit = kind_scope == "edit" and matched_family in {
        "concubine_heart",
        "divination",
        "duel",
        "explore_rift",
        "mulan_support",
        "wendao",
        "wild_training",
    }
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
        if not already_consumed and str(matched_family or "").startswith("tianxing_"):
            handled_any = apply_tianxing_passive(text, now=now, family=matched_family) or handled_any
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

        stargazer_miniapp_done = await handle_stargazer_miniapp_entry(
            event,
            text,
            now,
            reply_to,
            matched_family=matched_family,
            result_msg_id=event.id,
        )
        handled_any = stargazer_miniapp_done or handled_any
        trial_miniapp_done = await handle_trial_miniapp_entry(
            event,
            text,
            now,
            reply_to,
            matched_family=matched_family,
            result_msg_id=event.id,
        )
        handled_any = trial_miniapp_done or handled_any
        cave_treasure_miniapp_done = await handle_cave_treasure_miniapp_entry(
            event,
            text,
            now,
            reply_to,
            matched_family=matched_family,
            result_msg_id=event.id,
        )
        handled_any = cave_treasure_miniapp_done or handled_any
        tree_miniapp_done = await handle_tree_miniapp_entry(
            event,
            text,
            now,
            reply_to,
            matched_family=matched_family,
            result_msg_id=event.id,
        )
        handled_any = tree_miniapp_done or handled_any

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
            handled_any = await handle_pet_formation_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_ranch_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_checkin_reply(text, now, reply_to, matched_family=matched_family) or handled_any
            handled_any = await handle_sect_teach_reply(text, now, reply_to, matched_family=matched_family) or handled_any
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
            handled_any = await handle_mulan_reply(
                text,
                now,
                reply_to,
                matched_family=matched_family,
                result_msg_id=event.id,
            ) or handled_any
            handled_any = await handle_wanxin_reply(
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
            fishing_miniapp_handled = await handle_fishing_miniapp_entry(
                event,
                text,
                now,
                reply_to,
                matched_family=matched_family,
                result_msg_id=event.id,
            )
            handled_any = fishing_miniapp_handled or handled_any
            if not fishing_miniapp_handled:
                fishing_miniapp_held = await hold_unclaimed_fishing_miniapp_entry(
                    event,
                    text,
                    now,
                    result_msg_id=event.id,
                )
                handled_any = fishing_miniapp_held or handled_any
            if not fishing_miniapp_handled and not fishing_miniapp_held:
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


register_game_command_sent_observer(_observe_sent_for_early_reply_replay)


@client.on(events.NewMessage())
async def on_message(event):
    await observe_red_packet_candidate(event, event_type="message")
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

    if not _is_game_group_listener_event(event):
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
    if identity_sender_id:
        _refresh_identity_username_from_event(event, identity_sender_id)
    if raw_text.startswith(".") and not sender_is_game_bot:
        _observe_game_command_for_bot_evidence(sender_id, raw_text, event.id, now=now)
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
            observe_replica_game_command_message(event, identity_sender_id, now=now)
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

    text = event.raw_text or ""
    observe_dungeon_quiet_text(text, now=now)

    try:
        reply_to, reply_context = await _resolve_event_reply(event)
        _bind_command_attempt_shadow(event, text, now, reply_context, event_kind="message")
        _record_message_box_shadow(
            event,
            text,
            reply_context,
            reply_to=reply_to,
            event_type="message",
            is_game_bot=sender_is_game_bot,
            is_game_group=True,
        )
        # bot 健康证据必须来自指令关联回复；广播/通告仍解析，但不触发恢复。
        await _note_game_bot_activity(text, reply_to, reply_context, now=now)

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

        if await _dispatch_miniapp_broadcast_fallbacks(event, text, now):
            await handle_passive_module_card(from_telegram_event(event, text, {"routed_reply_handled": True, "family": "miniapp"}, event_kind="message"), now)
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
    await observe_red_packet_candidate(event, event_type="edit")
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

    if not _is_game_group_listener_event(event):
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

    now = time.time()
    text = event.raw_text or ""
    observe_dungeon_quiet_text(text, now=now)

    try:
        reply_to, reply_context = await _resolve_event_reply(event)
        _bind_command_attempt_shadow(event, text, now, reply_context, event_kind="edit")
        _record_message_box_shadow(
            event,
            text,
            reply_context,
            reply_to=reply_to,
            event_type="edit",
            is_game_bot=sender_is_game_bot,
            is_game_group=True,
        )
        # bot 健康证据必须来自指令关联回复；广播/通告仍解析，但不触发恢复。
        await _note_game_bot_activity(text, reply_to, reply_context, now=now)

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
        cleared_count = clear_transient_send_failures_for_global_recovery(now)
        spread_count = spread_overdue_runtime_timers(now, reason="启动初始化")
        if cleared_count or spread_count:
            extend_global_recovery_throttle_for_spread(now, reason="启动初始化")
        recover_divination_startup_timeouts(now)
        save_state()
    else:
        for identity_id in identity_ids:
            initialize_identity_runtime(identity_id, now)
            mark_dirty()
        cleared_count = clear_transient_send_failures_for_global_recovery(now)
        spread_count = spread_overdue_runtime_timers(now, reason="启动恢复")
        if cleared_count or spread_count:
            extend_global_recovery_throttle_for_spread(now, reason="启动恢复")
        recover_divination_startup_timeouts(now)
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


async def main_loop(stop_event=None, quiesce_event=None):
    while stop_event is None or not stop_event.is_set():
        if quiesce_event is not None and quiesce_event.is_set():
            _cancel_identity_schedulers()
            await _sleep_or_stop(stop_event, 1)
            continue
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
            await toggle_global_enabled(False, source="bot_health_monitor")
        if not get_global_enabled():
            _cancel_identity_schedulers()
            if get_global_pause_source() == MAINTENANCE_PAUSE_SOURCE:
                await run_miniapp_daily_scheduler(now)
            await _sleep_or_stop(stop_event, 5)
            continue

        await _run_phaseful_identity_schedulers(time.time())
        await _run_tianxing_daily_bootstrap_identity_schedulers(time.time())
        await _run_tianxing_timeline_followup_identity_schedulers(time.time())
        await _run_due_tianxing_schedulers(now)
        await _run_due_explore_rift_schedulers(now)
        await _run_due_wild_training_retry_schedulers(now)
        await _run_due_concubine_schedulers(now)
        await run_rare_daily_report_scheduler(now)
        await run_duel_daily_report_scheduler(now)
        await _run_global_schedulers(now)
        await run_quiz_learning_scheduler(now)
        await run_retry_scheduler(now)
        await run_identity_info_followup_scheduler(now)
        await _run_phaseful_identity_schedulers(time.time())
        _start_identity_schedulers_if_idle(now)
        await _sleep_or_stop(stop_event, 5)


def _quiesce_runtime(quiesce_event):
    set_game_send_quiesced(True)
    quiesce_event.set()
    _cancel_identity_schedulers()
    save_state()


async def main():
    global _log_bot_callback_task, _phaseful_scheduler_task, _small_world_scheduler_task
    stop_event = asyncio.Event()
    quiesce_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    set_game_send_quiesced(False)

    def request_stop():
        stop_event.set()

    def request_quiesce():
        _quiesce_runtime(quiesce_event)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, request_stop)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda _signum, _frame: request_stop())
    if hasattr(signal, "SIGUSR1"):
        try:
            loop.add_signal_handler(signal.SIGUSR1, request_quiesce)
        except (NotImplementedError, RuntimeError):
            signal.signal(signal.SIGUSR1, lambda _signum, _frame: request_quiesce())

    try:
        await bootstrap()
        _log_bot_callback_task = asyncio.create_task(
            run_log_bot_callback_poller(handle_replica_button_callback, stop_event)
        )
        _phaseful_scheduler_task = asyncio.create_task(_run_phaseful_scheduler_loop(stop_event))
        _small_world_scheduler_task = asyncio.create_task(_run_small_world_scheduler_loop(stop_event))
        await main_loop(stop_event, quiesce_event)
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
