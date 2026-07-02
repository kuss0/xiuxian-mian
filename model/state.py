import copy
import contextvars
from contextlib import contextmanager

from .config import (
    CHECKIN_WINDOW_END_HOUR_UTC,
    CHECKIN_WINDOW_START_HOUR_UTC,
    CMD_PET_WARM,
    CMD_PET_FORMATION,
    CMD_PET_TRIAL,
    DEFAULT_PET_NAME,
    DIVINATION_DEFAULT_DAILY_LIMIT,
    GAME_BOT_IDS,
    GAME_GROUP_ID,
    GAME_TOPIC_ID,
    GUANXING_SHIFT_START_DELAY_SEC,
    GUANXING_TARGET_KEYWORDS,
    MODULE_NAMES,
    STARGAZER_STAR_CHOICES,
    TIANTI_RANK_CHOICES,
    TOWER_WINDOW_END_HOUR_UTC,
    TOWER_WINDOW_START_HOUR_UTC,
    TZ_LOCAL,
    WILD_TRAINING_STRATEGIES,
)
from .module_manifest import is_module_archived

_current_identity_id = contextvars.ContextVar("current_identity_id", default=0)
_identity_context_active = contextvars.ContextVar("identity_context_active", default=False)

IDENTITY_MODULE_COLUMNS = [
    "tree_enabled", "pet_enabled", "pet_warm_enabled", "pet_trial_enabled", "pet_formation_enabled", "ranch_enabled", "wild_training_enabled", "stargazer_enabled", "guanxing_enabled", "formation_enabled", "tianti_enabled", "tianti_wenxin_enabled", "tianti_gangfeng_enabled", "quiz_enabled", "jiyin_enabled", "concubine_enabled", "concubine_tianji_enabled", "concubine_heart_enabled", "concubine_voyage_enabled", "concubine_auto_reacquire", "hehuan_enabled", "tianxing_enabled", "yinluo_enabled", "mulan_enabled", "world_boss_enabled", "nanlong_enabled", "yuanying_enabled", "explore_rift_enabled", "deep_retreat_enabled", "small_world_enabled", "small_world_preach_enabled", "small_world_manifest_enabled", "small_world_harvest_enabled", "small_world_refine_enabled", "small_world_refresh_enabled", "small_world_barrier_enabled", "small_world_barrier_min_stock", "small_world_barrier_guard_before_min", "small_world_barrier_min_interval_hours", "divination_enabled", "divination_daily_limit", "checkin_enabled", "sect_teach_enabled", "tower_enabled", "dungeon_join_enabled",
    "second_soul_enabled", "second_soul_auto_choice_enabled", "taiyi_enabled", "taiyi_node_search_enabled", "wendao_enabled", "duel_enabled", "fishing_enabled",
    "is_maturing", "is_invading", "is_harvested", "pending_irrigation", "tree_bootstrap_check_needed",
    "checkin_teach_count", "checkin_teach_day", "last_checkin_done_day", "last_tower_day", "last_guanxing_done_day",
]
IDENTITY_TIMER_COLUMNS = [
    "next_irr_time", "next_guard_time", "next_pet_time", "next_pet_warm_time", "next_pet_trial_time", "next_pet_formation_time", "next_ranch_time", "next_wild_training_time", "next_stargazer_panel_time", "stargazer_collect_due_at", "next_tianti_status_time", "next_tianti_wenxin_time", "next_tianti_climb_time", "next_tianti_gangfeng_time", "next_checkin_time", "next_sect_teach_time",
    "next_tower_time", "next_quiz_time", "next_jiyin_time", "next_concubine_time", "next_nanlong_time", "next_small_world_time", "next_yuanying_time", "next_explore_rift_time", "next_wendao_time", "next_mulan_time", "next_formation_time", "formation_cooldown_until", "next_deep_retreat_time",
    "next_second_soul_time", "second_soul_heart_demon_deadline", "next_duel_time", "next_fishing_time",
    "next_taiyi_cycle_time", "taiyi_phase_entered_at", "taiyi_freeze_until",
    "weak_until",
]
IDENTITY_RUNTIME_COLUMNS = [
    "sect_teach_reply_to_msg_id", "last_checkin_msg_id", "last_sect_teach_msg_id", "checkin_cleanup_msg_ids",
    "tree_maturing_logged", "tree_harvest_followup_due_at", "tree_harvest_inflight_until", "tree_last_harvest_result_msg_id", "tree_last_harvest_reply_to_msg_id", "tree_bootstrap_check_due_at", "last_tree_status_sent_at",
    "tree_pulse_mode_seen", "tree_pulse_last_panel_at", "tree_pulse_progress", "tree_pulse_main", "tree_pulse_aux", "tree_pulse_reverse", "tree_pulse_neutral", "tree_pulse_stability", "tree_pulse_stability_max", "tree_pulse_turbidity", "tree_pulse_turbidity_max", "tree_pulse_daily_used", "tree_pulse_daily_limit", "tree_pulse_rush_used", "tree_pulse_rush_limit", "tree_pulse_last_action", "tree_pulse_last_error", "tree_pulse_blocked_until",
    "last_tower_msg_id", "last_tower_command_sent_at", "tower_reply_due_at", "tower_retry_count", "pet_last_error", "pet_warm_last_error", "pet_trial_last_error", "pet_formation_last_error", "pet_formation_retry_count",
    "ranch_reply_to_msg_id", "ranch_reply_due_at", "ranch_retry_count", "ranch_last_msg_id", "ranch_last_result", "ranch_last_error", "ranch_return_pending", "ranch_return_seen_msg_id", "ranch_return_wait_since", "ranch_return_last_notified_at",
    "wild_training_strategy", "wild_training_reply_to_msg_id", "wild_training_reply_due_at", "wild_training_retry_count", "wild_training_last_msg_id", "wild_training_last_result", "wild_training_last_result_at", "wild_training_last_error", "wild_training_tianxing_prepare_retry_at",
    "stargazer_last_panel_msg_id", "stargazer_last_action", "stargazer_queued_action", "stargazer_idle_slot_count", "stargazer_dim_slot_count", "stargazer_ready_slot_count",
    "stargazer_busy_until", "stargazer_followup_due_at", "stargazer_wait_full_collect", "stargazer_collect_ready", "stargazer_soothe_before_collect",
    "guanxing_last_query_msg_id", "guanxing_last_panel_msg_id", "guanxing_panel_slot_key", "guanxing_last_panel_seen_at", "guanxing_last_shift_msg_id", "guanxing_last_shift_slot_key", "guanxing_last_shift_target", "guanxing_last_error",
    "last_formation_msg_id", "formation_pending_invite_msg_id", "formation_pending_assist_msg_id", "formation_last_action", "formation_last_result", "formation_last_error", "formation_last_success_at",
    "tianti_status_reply_to_msg_id", "tianti_last_status_msg_id", "tianti_last_status_seen_at", "tianti_last_wenxin_msg_id", "tianti_last_climb_msg_id", "tianti_last_gangfeng_msg_id", "tianti_progress_current", "tianti_progress_total", "tianti_cycle_count", "tianti_gangfeng_level", "tianti_gangfeng_total", "tianti_cooldown_text", "tianti_wenxin_status", "tianti_gangfeng_status", "tianti_remaining_climb_count", "tianti_last_wenxin_day", "tianti_wenxin_last_trigger_key", "tianti_gangfeng_last_trigger_key", "tianti_last_skip_reason", "tianti_theoretical_max_stage", "tianti_wenxin_trigger_stage", "tianti_last_cost_xiuwei", "tianti_last_gain_xiuwei", "tianti_last_gain_contrib", "tianti_last_error",
    "quiz_reply_to_msg_id", "quiz_chat_id", "quiz_question", "quiz_options", "quiz_answer", "quiz_phase", "quiz_retry_count", "quiz_match_mode", "quiz_answer_method", "quiz_last_error", "quiz_last_matched_at", "quiz_deadline_at",
    "jiyin_reply_to_msg_id", "jiyin_last_error",
    "concubine_phase", "concubine_availability", "concubine_nanlong_strategy", "concubine_status_msg_id", "concubine_greet_msg_id", "concubine_last_greet_day", "concubine_greet_retry_count", "concubine_greet_last_error", "concubine_gift_status_msg_id", "concubine_gift_bag_msg_id", "concubine_gift_msg_id", "concubine_gift_amount", "concubine_last_gift_day", "concubine_gift_attempt_day", "concubine_gift_last_error", "concubine_dream_msg_id", "concubine_fragment_msg_id", "concubine_puzzle_msg_id", "concubine_reacquire_msg_id", "concubine_tianji_msg_id", "concubine_heart_msg_id", "concubine_heart_prompt_msg_id", "concubine_voyage_msg_id", "concubine_voyage_retry_count", "concubine_last_panel_msg_id", "concubine_name", "concubine_kind", "concubine_location", "concubine_affinity", "concubine_oath", "concubine_dream_due_at", "concubine_tianji_due_at", "concubine_heart_due_at", "concubine_tianji_chain", "concubine_tianji_chain_due_at", "concubine_heart_round", "concubine_heart_choice_prompt_msg_id", "concubine_heart_choice_round", "concubine_heart_choice_sent_at", "concubine_heart_choice_retry_count", "concubine_last_recovered_reply_key", "concubine_last_recovered_reply_at", "concubine_fragment_count", "concubine_fragment_total", "concubine_fragment_xutian_count", "concubine_fragment_xutian_total", "concubine_fragment_cangkun_count", "concubine_fragment_cangkun_total", "concubine_fragment_confirm_key", "concubine_fragment_confirmed_at", "concubine_voyage_status", "concubine_voyage_route", "concubine_voyage_return_at", "concubine_voyage_last_result", "concubine_voyage_last_error", "concubine_last_snapshot_at", "concubine_reacquire_blocked_until", "concubine_reacquire_attempts", "concubine_reacquire_command_override", "concubine_last_error", "concubine_tianji_last_error", "concubine_heart_last_error",
    "hehuan_observation", "tianxing_observation", "tianxing_auto_config", "tianxing_timeline_state", "yinluo_observation",
    "world_boss_action_count", "world_boss_action_limit", "world_boss_attack_count", "world_boss_pending_msg_id", "world_boss_pending_action", "world_boss_pending_since", "world_boss_pending_retry_count", "world_boss_pending_action_seq", "world_boss_last_action", "world_boss_last_action_at", "world_boss_last_reply_msg_id", "world_boss_exhausted", "world_boss_last_error",
    "nanlong_reply_to_msg_id", "nanlong_reply_due_at", "nanlong_last_msg_id", "nanlong_retry_count", "nanlong_last_command", "nanlong_protect_phase", "nanlong_place_msg_id", "nanlong_recall_msg_id", "nanlong_last_error",
    "small_world_preach_reply_to_msg_id", "small_world_preach_due_at", "small_world_god_cooldown_until", "small_world_pending_god_action", "small_world_pending_god_reason", "small_world_pending_god_priority", "small_world_pending_god_at", "small_world_last_god_action", "small_world_last_god_sent_at", "small_world_last_disaster_wave_at", "small_world_barrier_msg_id", "small_world_barrier_due_at", "small_world_last_barrier_sent_at", "small_world_phase", "small_world_query_msg_id", "small_world_manifest_msg_id", "small_world_manifest_cost_text", "small_world_harvest_msg_id", "small_world_refine_msg_id", "small_world_refresh_count", "small_world_pending_incense", "small_world_incense_stock", "small_world_faith_value", "small_world_panel_snapshot", "small_world_last_panel_at", "small_world_last_error",
    "resource_shortage_backoffs", "action_guard_sessions",
    "yuanying_phase", "yuanying_probe_pending", "yuanying_waiting_logged", "yuanying_protect_logged", "yuanying_summary_sent_at", "last_yuanying_summary_msg_id", "last_yuanying_command_time",
    "explore_rift_reply_to_msg_id", "explore_rift_reply_due_at", "explore_rift_pending_result_msg_id", "explore_rift_last_msg_id", "explore_rift_last_result", "explore_rift_last_error", "explore_rift_last_result_key", "explore_rift_manual_required", "explore_rift_tianxing_prepare_retry_at",
    "explore_rift_nascent_escape_weak_until", "explore_rift_rebirth_required", "explore_rift_rebirth_phase", "explore_rift_rebirth_due_at", "explore_rift_rebirth_request_msg_id", "explore_rift_rebirth_options_msg_id", "explore_rift_rebirth_select_msg_id", "explore_rift_rebirth_options_text", "explore_rift_rebirth_selected_index", "explore_rift_rebirth_last_result", "explore_rift_rebirth_last_error", "explore_rift_rebirth_choice_mode", "explore_rift_rebirth_preferred_root_type", "explore_rift_rebirth_preferred_attrs", "explore_rift_rebirth_blind_index", "explore_rift_fatal_msg_id", "explore_rift_fatal_confirm_due_at",
    "wendao_reply_to_msg_id", "wendao_reply_due_at", "wendao_pending_result_msg_id", "wendao_sent_at", "wendao_last_msg_id", "wendao_last_result", "wendao_last_error",
    "mulan_phase", "mulan_reply_to_msg_id", "mulan_reply_due_at", "mulan_pending_ids", "mulan_report_texts", "mulan_current_id", "mulan_public_id", "mulan_public_text", "mulan_support_action", "mulan_sent_at", "mulan_last_msg_id", "mulan_last_command", "mulan_last_result", "mulan_last_error", "mulan_cycle_count",
    "duel_target", "duel_total_count", "duel_completed_count", "duel_reply_to_msg_id", "duel_reply_due_at", "duel_open_msg_id", "duel_magic_due_at", "duel_magic_sent_at", "duel_started_at", "duel_last_msg_id", "duel_last_result", "duel_last_error",
    "fishing_pond", "fishing_bait", "fishing_daily_limit", "fishing_daily_day", "fishing_daily_count", "fishing_basket_calibrated_day", "fishing_auto_chum_enabled", "fishing_chum_name", "fishing_chum_names", "fishing_chum_day", "fishing_chum_counts", "fishing_auto_buy_bait_enabled", "fishing_auto_buy_bait_count", "fishing_auto_probe_enabled", "fishing_auto_open_fish_enabled", "fishing_cancel_after_sec", "fishing_transfer_target_id", "fishing_transfer_due_at", "fishing_caught_fish_json", "fishing_valuable_drop_reminders", "fishing_phase", "fishing_reply_to_msg_id", "fishing_reply_due_at", "fishing_status_msg_id", "fishing_pending_action", "fishing_pending_open_fish", "fishing_forced_buy_bait", "fishing_forced_buy_count", "fishing_started_at", "fishing_active_chum_name", "fishing_chum_rods_remaining", "fishing_last_msg_id", "fishing_last_result", "fishing_last_error",
    "deep_retreat_phase", "deep_retreat_probe_pending", "deep_retreat_waiting_logged", "deep_retreat_protect_logged", "deep_retreat_summary_sent_at", "last_deep_retreat_summary_msg_id", "last_deep_retreat_command_time",
    "second_soul_phase", "second_soul_choice_strategy", "second_soul_heart_demon_msg_id", "second_soul_heart_demon_notified", "second_soul_status_msg_id", "second_soul_train_msg_id",
    "second_soul_last_train_started_at", "second_soul_last_broadcast_key", "second_soul_last_broadcast_at", "second_soul_moran_value",
    "second_soul_purge_msg_id", "second_soul_purge_status_msg_id", "second_soul_purge_attempts", "second_soul_purge_due_at", "second_soul_purge_last_at", "second_soul_last_error",
    "taiyi_yindao_element", "taiyi_phase", "taiyi_pending_node_name", "taiyi_yindao_msg_id", "taiyi_node_search_msg_id", "taiyi_node_define_msg_id", "taiyi_freeze_reason", "taiyi_failure_history", "taiyi_yindao_resend_count", "taiyi_search_resend_count", "taiyi_last_error",
    "weak_reason", "weak_source", "weak_last_block_log_at",
    "identity_info_reply_msg_ids", "last_identity_info_msg_id", "identity_info_last_error", "identity_info_last_requested_at", "identity_info_followup_due_at", "identity_info_primary_payload",
]
IDENTITY_JSON_COLUMNS = {"checkin_cleanup_msg_ids", "identity_info_reply_msg_ids", "quiz_options", "identity_info_primary_payload", "hehuan_observation", "tianxing_observation", "tianxing_auto_config", "tianxing_timeline_state", "yinluo_observation", "taiyi_failure_history", "small_world_panel_snapshot", "resource_shortage_backoffs", "action_guard_sessions", "fishing_valuable_drop_reminders", "mulan_report_texts"}
IDENTITY_BOOL_FIELDS = {
    "tree_enabled", "pet_enabled", "pet_warm_enabled", "pet_trial_enabled", "pet_formation_enabled", "ranch_enabled", "wild_training_enabled", "stargazer_enabled", "guanxing_enabled", "formation_enabled", "tianti_enabled", "tianti_wenxin_enabled", "tianti_gangfeng_enabled", "quiz_enabled", "jiyin_enabled", "concubine_enabled", "concubine_tianji_enabled", "concubine_heart_enabled", "concubine_voyage_enabled", "concubine_auto_reacquire", "hehuan_enabled", "tianxing_enabled", "yinluo_enabled", "mulan_enabled", "world_boss_enabled", "nanlong_enabled", "yuanying_enabled", "explore_rift_enabled", "deep_retreat_enabled", "small_world_enabled", "small_world_preach_enabled", "small_world_manifest_enabled", "small_world_harvest_enabled", "small_world_refine_enabled", "small_world_refresh_enabled", "small_world_barrier_enabled", "divination_enabled", "checkin_enabled", "sect_teach_enabled", "tower_enabled", "dungeon_join_enabled",
    "second_soul_enabled", "second_soul_auto_choice_enabled", "taiyi_enabled", "taiyi_node_search_enabled", "wendao_enabled", "duel_enabled", "fishing_enabled",
    "fishing_auto_chum_enabled", "fishing_auto_buy_bait_enabled", "fishing_auto_probe_enabled", "fishing_auto_open_fish_enabled",
    "is_maturing", "is_invading", "is_harvested", "pending_irrigation", "tree_bootstrap_check_needed",
    "stargazer_wait_full_collect", "stargazer_collect_ready", "stargazer_soothe_before_collect",
    "yuanying_probe_pending", "yuanying_waiting_logged", "yuanying_protect_logged", "deep_retreat_probe_pending", "deep_retreat_waiting_logged", "deep_retreat_protect_logged",
    "second_soul_heart_demon_notified",
    "explore_rift_manual_required", "explore_rift_rebirth_required",
    "tree_maturing_logged", "world_boss_exhausted",
}
META_STATE_KEYS = {"my_user_id", "game_group_id", "game_bot_ids", "game_listener_account_ids", "game_topic_id", "forum_topics", "forum_topics_updated_at", "auto_delete_sent_messages", "global_enabled", "tiandao_judgement_enabled", "tiandao_judgement_pending", "tianji_quiz_pending", "divination_pending_exchanges", "divination_run_state", "world_boss_run_state", "guanxing_monitor_enabled", "guanxing_monitor_targets", "guanxing_shift_target", "guanxing_shift_delay_sec", "next_guanxing_monitor_notify_time", "guanxing_monitor_slot_key", "guanxing_monitor_slot_start_at", "guanxing_monitor_slot_end_at", "guanxing_monitor_seen_panel", "guanxing_monitor_matched_keyword", "guanxing_monitor_matched_value", "guanxing_monitor_last_evolution_value", "guanxing_monitor_last_seen_at", "guanxing_monitor_last_notified_slot_key", "guanxing_round_state", "formation_run_state", "replica_group_id", "replica_group_ids", "replica_listener_account_id", "replica_listener_account_map", "replica_dispatch_group_ids", "replica_dispatch_listener_account_map", "replica_participant_identity_ids", "replica_dispatch_participant_identity_ids", "replica_run_state", "replica_virtual_hall_match_enabled_map", "replica_query_aggregator_config", "replica_success_cooldown_hours", "storage_bag_api_config", "storage_bag_records", "storage_bag_item_rules", "tianjige_dao_path_records", "dungeon_join_run_state", "dungeon_quiet_until", "dungeon_quiet_reason", "dungeon_quiet_last_log_at", "mulan_intel_state", "send_as_profiles", "identity_states", "identity_ids", "quiz_learning_watchers", "quiz_ai_config", "accounts", "identity_account_map", "identity_membership_initialized", "delayed_actions_state"}
REPLICA_SUCCESS_COOLDOWN_HOUR_DEFAULTS = {
    "cangkun": 2.5,
}
QUIZ_AI_CONFIG_DEFAULTS = {
    "enabled": False,
    "auto_answer_enabled": False,
    "provider": "codex",
    "base_url": "",
    "model": "",
    "api_key": "",
    "confidence_threshold": 0.8,
    "timeout_sec": 20,
    "decision_timeout_sec": 20,
    "answer_safety_margin_sec": 12,
    "temperature": 0,
    "providers": [],
    "last_question": "",
    "last_answer": "",
    "last_confidence": 0,
    "last_reason": "",
    "last_error": "",
    "last_provider": "",
    "last_results": [],
    "last_vote_summary": "",
    "last_provider_count": 0,
    "last_valid_count": 0,
    "last_decision_timeout_sec": 0,
    "last_updated_at": 0,
}
SEND_AS_PROFILE_DEFAULTS = {
    "username": "",
    "label": "",
    "daohao": "",
    "realm": "",
    "spiritual_root_type": "",
    "spiritual_root_attrs": "",
    "replica_professions": "",
    "replica_gold_dps_enabled": False,
    "pet_name": DEFAULT_PET_NAME,
    "pet_warm_name": "",
    "pet_trial_name": "",
    "sect_name": "",
    "sect_updated_at": 0,
    "sect_contribution": 0,
    "sect_contribution_updated_at": 0,
    "xiuwei_current": 0,
    "xiuwei_max": 0,
    "battle_power_text": "",
    "battle_power_value": 0,
    "jiyin_choice": "",
    "nanlong_choice": "reject",
    "stargazer_star_choice": STARGAZER_STAR_CHOICES[0],
    "tianti_rank_choice": TIANTI_RANK_CHOICES[0],
    "stargazer_total_slots": 0,
    "checkin_window_start_hour_utc": CHECKIN_WINDOW_START_HOUR_UTC,
    "checkin_window_end_hour_utc": CHECKIN_WINDOW_END_HOUR_UTC,
    "tower_window_start_hour_utc": TOWER_WINDOW_START_HOUR_UTC,
    "tower_window_end_hour_utc": TOWER_WINDOW_END_HOUR_UTC,
    "enabled": True,
}
REALM_SORT_ORDER = [
    "炼气一层",
    "炼气二层",
    "炼气三层",
    "炼气四层",
    "炼气五层",
    "炼气六层",
    "炼气七层",
    "炼气八层",
    "炼气九层",
    "炼气十层",
    "炼气十一层",
    "炼气十二层",
    "炼气十三层",
    "筑基初期",
    "筑基中期",
    "筑基后期",
    "结丹初期",
    "结丹中期",
    "结丹后期",
    "元婴初期",
    "元婴中期",
    "元婴后期",
    "化神初期",
    "化神中期",
    "化神后期",
    "化神后期大圆满",
]
REALM_SORT_INDEX = {realm: index for index, realm in enumerate(REALM_SORT_ORDER)}
NO_SECT_NAMES = {"", "散修", "无", "无宗门", "未加入宗门", "未记录", "未知"}
GENERIC_SECT_MODULE_NAMES = {"点卯", "宗门传功", "闯塔"}
SECT_MODULE_REQUIREMENTS = {
    "灵树": "落云宗",
    "观星台": "星宫",
    "观星": "星宫",
    "周天星斗": "星宫",
    "登天阶": "凌霄宫",
    "太一": "太一门",
    "放养": "万灵宗",
    "合欢宗": "合欢宗",
    "天星宗": "天星宗",
    "阴罗宗": "阴罗宗",
    "问道": "元婴宗",
}
REPLICA_PROFESSION_RULES = [
    ("御山", {"土"}),
    ("灵医", {"木", "水"}),
    ("影刃", {"风", "冰"}),
    ("破军", {"金", "雷"}),
    ("咒师", {"火", "暗"}),
]
REALM_XIUWEI_MAX_MAP = {
    100: "炼气一层",
    150: "炼气二层",
    220: "炼气三层",
    300: "炼气四层",
    400: "炼气五层",
    520: "炼气六层",
    650: "炼气七层",
    800: "炼气八层",
    1000: "炼气九层",
    1250: "炼气十层",
    1500: "炼气十一层",
    1800: "炼气十二层",
    2200: "炼气十三层",
    5000: "筑基初期",
    10000: "筑基中期",
    30000: "筑基后期",
    50000: "结丹初期",
    100000: "结丹中期",
    200000: "结丹后期",
    500000: "元婴初期",
    1000000: "元婴中期",
    2000000: "元婴后期",
    4000000: "化神初期",
    8000000: "化神中期",
    16000000: "化神后期",
    32000000: "化神后期大圆满",
}
YUANYING_MIN_REALM = "元婴初期"
YUANYING_MIN_REALM_INDEX = REALM_SORT_INDEX[YUANYING_MIN_REALM]
SMALL_WORLD_MIN_REALM = "化神初期"
SMALL_WORLD_MIN_REALM_INDEX = REALM_SORT_INDEX[SMALL_WORLD_MIN_REALM]


def infer_realm_from_xiuwei_max(xiuwei_max):
    try:
        xiuwei_max = int(xiuwei_max or 0)
    except (TypeError, ValueError):
        return ""
    return REALM_XIUWEI_MAX_MAP.get(xiuwei_max, "")

IDENTITY_STATE_TEMPLATE = {
    # 业务对象开关（新身份默认全关，需手动开启）
    "tree_enabled": False,
    "pet_enabled": False,
    "pet_warm_enabled": False,
    "pet_trial_enabled": False,
    "pet_formation_enabled": False,
    "ranch_enabled": False,
    "wild_training_enabled": False,
    "quiz_enabled": False,
    "jiyin_enabled": False,
    "concubine_enabled": False,
    "concubine_tianji_enabled": False,
    "concubine_heart_enabled": False,
    "concubine_voyage_enabled": False,
    "concubine_auto_reacquire": True,
    "hehuan_enabled": False,
    "tianxing_enabled": False,
    "yinluo_enabled": False,
    "mulan_enabled": False,
    "world_boss_enabled": False,
    "nanlong_enabled": False,
    "tianti_enabled": False,
    "tianti_wenxin_enabled": True,
    "tianti_gangfeng_enabled": True,
    "yuanying_enabled": False,
    "explore_rift_enabled": False,
    "wendao_enabled": False,
    "duel_enabled": False,
    "fishing_enabled": False,
    "formation_enabled": False,
    "deep_retreat_enabled": False,
    "small_world_enabled": False,
    "small_world_preach_enabled": False,
    "small_world_manifest_enabled": False,
    "small_world_harvest_enabled": False,
    "small_world_refine_enabled": False,
    "small_world_refresh_enabled": False,
    "small_world_barrier_enabled": True,
    "small_world_barrier_min_stock": 130000,
    "small_world_barrier_guard_before_min": 30,
    "small_world_barrier_min_interval_hours": 18,
    "divination_enabled": False,
    "divination_daily_limit": DIVINATION_DEFAULT_DAILY_LIMIT,
    "checkin_enabled": False,
    "sect_teach_enabled": False,
    "tower_enabled": False,
    "dungeon_join_enabled": False,
    "stargazer_enabled": False,
    "guanxing_enabled": False,

    # 灵树模块
    "next_irr_time": 0,
    "next_guard_time": 0,
    "is_maturing": False,
    "is_invading": False,
    "is_harvested": False,
    "pending_irrigation": False,
    "tree_bootstrap_check_needed": False,
    "tree_pulse_mode_seen": False,
    "tree_pulse_last_panel_at": 0,
    "tree_pulse_progress": 0.0,
    "tree_pulse_main": "",
    "tree_pulse_aux": "",
    "tree_pulse_reverse": "",
    "tree_pulse_neutral": "",
    "tree_pulse_stability": 0,
    "tree_pulse_stability_max": 0,
    "tree_pulse_turbidity": 0,
    "tree_pulse_turbidity_max": 0,
    "tree_pulse_daily_used": 0,
    "tree_pulse_daily_limit": 0,
    "tree_pulse_rush_used": 0,
    "tree_pulse_rush_limit": 0,
    "tree_pulse_last_action": "",
    "tree_pulse_last_error": "",
    "tree_pulse_blocked_until": 0,

    # 灵树运行态（不持久化）
    "tree_maturing_logged": False,
    "tree_harvest_followup_due_at": 0,
    "tree_harvest_inflight_until": 0,
    "tree_last_harvest_result_msg_id": 0,
    "tree_last_harvest_reply_to_msg_id": 0,
    "tree_bootstrap_check_due_at": 0,
    "last_tree_status_sent_at": 0,

    # 法宝模块
    "next_pet_time": 0,
    "next_pet_warm_time": 0,
    "next_pet_trial_time": 0,
    "next_pet_formation_time": 0,
    "pet_last_error": "",
    "pet_warm_last_error": "",
    "pet_trial_last_error": "",
    "pet_formation_last_error": "",
    "pet_formation_retry_count": 0,

    # 放养/野外历练模块
    "next_ranch_time": 0,
    "ranch_reply_to_msg_id": 0,
    "ranch_reply_due_at": 0,
    "ranch_retry_count": 0,
    "ranch_last_msg_id": 0,
    "ranch_last_result": "",
    "ranch_last_error": "",
    "ranch_return_pending": False,
    "ranch_return_seen_msg_id": 0,
    "ranch_return_wait_since": 0,
    "ranch_return_last_notified_at": 0,
    "next_wild_training_time": 0,
    "wild_training_strategy": "谨慎",
    "wild_training_reply_to_msg_id": 0,
    "wild_training_reply_due_at": 0,
    "wild_training_retry_count": 0,
    "wild_training_last_msg_id": 0,
    "wild_training_last_result": "",
    "wild_training_last_result_at": 0,
    "wild_training_last_error": "",
    "wild_training_tianxing_prepare_retry_at": 0,

    # 观星台模块
    "next_stargazer_panel_time": 0,
    "stargazer_collect_due_at": 0,
    "stargazer_last_panel_msg_id": 0,
    "stargazer_last_action": "",
    "stargazer_queued_action": "",
    "stargazer_idle_slot_count": 0,
    "stargazer_dim_slot_count": 0,
    "stargazer_ready_slot_count": 0,
    "stargazer_busy_until": 0,
    "stargazer_followup_due_at": 0,
    "stargazer_wait_full_collect": False,
    "stargazer_collect_ready": False,
    "stargazer_soothe_before_collect": False,

    # 观星模块
    "guanxing_last_query_msg_id": 0,
    "guanxing_last_panel_msg_id": 0,
    "guanxing_panel_slot_key": "",
    "guanxing_last_panel_seen_at": 0,
    "guanxing_last_shift_msg_id": 0,
    "guanxing_last_shift_slot_key": "",
    "guanxing_last_shift_target": "",
    "guanxing_last_error": "",

    # 周天星斗模块
    "next_formation_time": 0,
    "formation_cooldown_until": 0,
    "last_formation_msg_id": 0,
    "formation_pending_invite_msg_id": 0,
    "formation_pending_assist_msg_id": 0,
    "formation_last_action": "",
    "formation_last_result": "",
    "formation_last_error": "",
    "formation_last_success_at": 0,

    # 登天阶模块
    "next_tianti_status_time": 0,
    "next_tianti_wenxin_time": 0,
    "next_tianti_climb_time": 0,
    "next_tianti_gangfeng_time": 0,
    "tianti_status_reply_to_msg_id": 0,
    "tianti_last_status_msg_id": 0,
    "tianti_last_status_seen_at": 0,
    "tianti_last_wenxin_msg_id": 0,
    "tianti_last_climb_msg_id": 0,
    "tianti_last_gangfeng_msg_id": 0,
    "tianti_progress_current": 0,
    "tianti_progress_total": 12,
    "tianti_cycle_count": 0,
    "tianti_gangfeng_level": 0,
    "tianti_gangfeng_total": 12,
    "tianti_cooldown_text": "未记录",
    "tianti_wenxin_status": "未记录",
    "tianti_gangfeng_status": "未记录",
    "tianti_remaining_climb_count": 0,
    "tianti_last_wenxin_day": "",
    "tianti_wenxin_last_trigger_key": "",
    "tianti_gangfeng_last_trigger_key": "",
    "tianti_last_skip_reason": "",
    "tianti_theoretical_max_stage": 0,
    "tianti_wenxin_trigger_stage": 0,
    "tianti_last_cost_xiuwei": 0,
    "tianti_last_gain_xiuwei": 0,
    "tianti_last_gain_contrib": 0,
    "tianti_last_error": "",

    # 点卯模块
    "next_checkin_time": 0,
    "checkin_teach_count": 0,
    "checkin_teach_day": "",
    "last_checkin_done_day": "",
    "next_sect_teach_time": 0,
    "sect_teach_reply_to_msg_id": 0,
    "last_checkin_msg_id": 0,
    "last_sect_teach_msg_id": 0,
    "checkin_cleanup_msg_ids": [],

    # 闯塔模块
    "next_tower_time": 0,
    "last_tower_day": "",
    "last_tower_msg_id": 0,
    "last_tower_command_sent_at": 0,
    "tower_reply_due_at": 0,
    "tower_retry_count": 0,

    # 观星模块
    "last_guanxing_done_day": "",

    # 玄骨考校模块
    "next_quiz_time": 0,
    "quiz_reply_to_msg_id": 0,
    "quiz_chat_id": 0,
    "quiz_question": "",
    "quiz_options": {},
    "quiz_answer": "",
    "quiz_phase": "",
    "quiz_retry_count": 0,
    "quiz_match_mode": "",
    "quiz_answer_method": "",
    "quiz_last_error": "",
    "quiz_last_matched_at": 0,
    "quiz_deadline_at": 0,

    # 极阴祖师模块
    "next_jiyin_time": 0,
    "jiyin_reply_to_msg_id": 0,
    "jiyin_last_error": "",

    # 侍妾模块
    "next_concubine_time": 0,
    "concubine_phase": "idle",  # idle|status_pending|greet_pending|gift_status_pending|gift_bag_pending|gift_pending|dream_pending|fragment_pending|puzzle_ready|puzzle_pending|reacquire_pending|tianji_pending|heart_pending|heart_choice_pending|voyage_pending|voyage_return_pending|no_partner
    "concubine_availability": "unknown",
    "concubine_nanlong_strategy": "reacquire_after_loss",
    "concubine_status_msg_id": 0,
    "concubine_greet_msg_id": 0,
    "concubine_last_greet_day": "",
    "concubine_greet_retry_count": 0,
    "concubine_greet_last_error": "",
    "concubine_gift_status_msg_id": 0,
    "concubine_gift_bag_msg_id": 0,
    "concubine_gift_msg_id": 0,
    "concubine_gift_amount": 0,
    "concubine_last_gift_day": "",
    "concubine_gift_attempt_day": "",
    "concubine_gift_last_error": "",
    "concubine_dream_msg_id": 0,
    "concubine_fragment_msg_id": 0,
    "concubine_puzzle_msg_id": 0,
    "concubine_reacquire_msg_id": 0,
    "concubine_tianji_msg_id": 0,
    "concubine_heart_msg_id": 0,
    "concubine_heart_prompt_msg_id": 0,
    "concubine_voyage_msg_id": 0,
    "concubine_voyage_retry_count": 0,
    "concubine_last_panel_msg_id": 0,
    "concubine_name": "",
    "concubine_kind": "",
    "concubine_location": "",
    "concubine_affinity": 0,
    "concubine_oath": "",
    "concubine_dream_due_at": 0,
    "concubine_tianji_due_at": 0,
    "concubine_heart_due_at": 0,
    "concubine_tianji_chain": "",
    "concubine_tianji_chain_due_at": 0,
    "concubine_heart_round": 0,
    "concubine_heart_choice_prompt_msg_id": 0,
    "concubine_heart_choice_round": 0,
    "concubine_heart_choice_sent_at": 0,
    "concubine_heart_choice_retry_count": 0,
    "concubine_last_recovered_reply_key": "",
    "concubine_last_recovered_reply_at": 0,
    "concubine_fragment_count": 0,
    "concubine_fragment_total": 4,
    "concubine_fragment_xutian_count": 0,
    "concubine_fragment_xutian_total": 4,
    "concubine_fragment_cangkun_count": 0,
    "concubine_fragment_cangkun_total": 4,
    "concubine_fragment_confirm_key": "",
    "concubine_fragment_confirmed_at": 0,
    "concubine_voyage_status": "",
    "concubine_voyage_route": "",
    "concubine_voyage_return_at": 0,
    "concubine_voyage_last_result": "",
    "concubine_voyage_last_error": "",
    "concubine_last_snapshot_at": 0,
    "concubine_reacquire_blocked_until": 0,
    "concubine_reacquire_attempts": 0,
    "concubine_reacquire_command_override": "",
    "concubine_last_error": "",
    "concubine_tianji_last_error": "",
    "concubine_heart_last_error": "",

    # 合欢宗模块（被动观察）
    "hehuan_observation": {},

    # 天星宗/阴罗宗模块（被动观察）
    "tianxing_observation": {},
    "tianxing_auto_config": {},
    "tianxing_timeline_state": {},
    "yinluo_observation": {},

    # 真仙试锋世界事件
    "world_boss_action_count": 0,
    "world_boss_action_limit": 5,
    "world_boss_attack_count": 0,
    "world_boss_pending_msg_id": 0,
    "world_boss_pending_action": "",
    "world_boss_pending_since": 0,
    "world_boss_pending_retry_count": 0,
    "world_boss_pending_action_seq": 0,
    "world_boss_last_action": "",
    "world_boss_last_action_at": 0,
    "world_boss_last_reply_msg_id": 0,
    "world_boss_exhausted": False,
    "world_boss_last_error": "",

    # 南陇侯模块
    "next_nanlong_time": 0,
    "nanlong_reply_to_msg_id": 0,
    "nanlong_reply_due_at": 0,
    "nanlong_last_msg_id": 0,
    "nanlong_retry_count": 0,
    "nanlong_last_command": "",
    "nanlong_protect_phase": "",
    "nanlong_place_msg_id": 0,
    "nanlong_recall_msg_id": 0,
    "nanlong_last_error": "",

    # 元婴模块
    "yuanying_phase": "idle",  # idle|queued_launch|launching|running|summary_due|observing_summary|waiting_summary|post_summary_wait
    "next_yuanying_time": 0,
    "yuanying_probe_pending": False,
    "yuanying_summary_sent_at": 0,
    "last_yuanying_summary_msg_id": 0,
    "last_yuanying_command_time": 0,

    # 探寻裂缝模块
    "next_explore_rift_time": 0,
    "explore_rift_reply_to_msg_id": 0,
    "explore_rift_reply_due_at": 0,
    "explore_rift_pending_result_msg_id": 0,
    "explore_rift_last_msg_id": 0,
    "explore_rift_last_result": "",
    "explore_rift_last_error": "",
    "explore_rift_last_result_key": "",
    "explore_rift_manual_required": False,
    "explore_rift_tianxing_prepare_retry_at": 0,
    "explore_rift_nascent_escape_weak_until": 0,
    "explore_rift_rebirth_required": False,
    "explore_rift_rebirth_phase": "idle",
    "explore_rift_rebirth_due_at": 0,
    "explore_rift_rebirth_request_msg_id": 0,
    "explore_rift_rebirth_options_msg_id": 0,
    "explore_rift_rebirth_select_msg_id": 0,
    "explore_rift_rebirth_options_text": "",
    "explore_rift_rebirth_selected_index": 0,
    "explore_rift_rebirth_last_result": "",
    "explore_rift_rebirth_last_error": "",
    "explore_rift_rebirth_choice_mode": "safe_first",
    "explore_rift_rebirth_preferred_root_type": "",
    "explore_rift_rebirth_preferred_attrs": "",
    "explore_rift_rebirth_blind_index": 1,
    "explore_rift_fatal_msg_id": 0,
    "explore_rift_fatal_confirm_due_at": 0,

    # 元婴宗问道模块
    "next_wendao_time": 0,
    "wendao_reply_to_msg_id": 0,
    "wendao_reply_due_at": 0,
    "wendao_pending_result_msg_id": 0,
    "wendao_sent_at": 0,
    "wendao_last_msg_id": 0,
    "wendao_last_result": "",
    "wendao_last_error": "",

    # 慕兰谍影模块
    "next_mulan_time": 0,
    "mulan_phase": "idle",  # idle|collect_pending|ready_to_judge|judge_pending|ready_to_publish|publish_pending|panel_pending|ready_to_support|support_pending|cooldown
    "mulan_reply_to_msg_id": 0,
    "mulan_reply_due_at": 0,
    "mulan_pending_ids": "",
    "mulan_report_texts": {},
    "mulan_current_id": 0,
    "mulan_public_id": 0,
    "mulan_public_text": "",
    "mulan_support_action": "",
    "mulan_sent_at": 0,
    "mulan_last_msg_id": 0,
    "mulan_last_command": "",
    "mulan_last_result": "",
    "mulan_last_error": "",
    "mulan_cycle_count": 0,

    # 斗法模块（实际发起命令为 .斗法）
    "next_duel_time": 0,
    "duel_target": "",
    "duel_total_count": 0,
    "duel_completed_count": 0,
    "duel_reply_to_msg_id": 0,
    "duel_reply_due_at": 0,
    "duel_open_msg_id": 0,
    "duel_magic_due_at": 0,
    "duel_magic_sent_at": 0,
    "duel_started_at": 0,
    "duel_last_msg_id": 0,
    "duel_last_result": "",
    "duel_last_error": "",

    # 灵溪垂钓模块
    "next_fishing_time": 0,
    "fishing_pond": "青溪浅滩",
    "fishing_bait": "凡饵",
    "fishing_daily_limit": 20,
    "fishing_daily_day": "",
    "fishing_daily_count": 0,
    "fishing_basket_calibrated_day": "",
    "fishing_auto_chum_enabled": True,
    "fishing_chum_name": "米糠小窝",
    "fishing_chum_names": "[\"米糠小窝\"]",
    "fishing_chum_day": "",
    "fishing_chum_counts": "",
    "fishing_auto_buy_bait_enabled": True,
    "fishing_auto_buy_bait_count": 20,
    "fishing_auto_probe_enabled": False,
    "fishing_auto_open_fish_enabled": True,
    "fishing_cancel_after_sec": 120,
    "fishing_transfer_target_id": 0,
    "fishing_transfer_due_at": 0,
    "fishing_caught_fish_json": "",
    "fishing_valuable_drop_reminders": [],
    "fishing_phase": "idle",
    "fishing_reply_to_msg_id": 0,
    "fishing_reply_due_at": 0,
    "fishing_status_msg_id": 0,
    "fishing_pending_action": "",
    "fishing_pending_open_fish": "",
    "fishing_forced_buy_bait": "",
    "fishing_forced_buy_count": 0,
    "fishing_started_at": 0,
    "fishing_active_chum_name": "",
    "fishing_chum_rods_remaining": 0,
    "fishing_last_msg_id": 0,
    "fishing_last_result": "",
    "fishing_last_error": "",

    # 深度闭关模块
    "deep_retreat_phase": "idle",  # idle|queued_launch|launching|running|summary_due|observing_summary|waiting_summary|post_summary_wait
    "next_deep_retreat_time": 0,
    "deep_retreat_probe_pending": False,
    "deep_retreat_summary_sent_at": 0,
    "last_deep_retreat_summary_msg_id": 0,
    "last_deep_retreat_command_time": 0,

    # 小世界模块
    "next_small_world_time": 0,
    "small_world_preach_reply_to_msg_id": 0,
    "small_world_preach_due_at": 0,
    "small_world_god_cooldown_until": 0,
    "small_world_pending_god_action": "",
    "small_world_pending_god_reason": "",
    "small_world_pending_god_priority": 0,
    "small_world_pending_god_at": 0,
    "small_world_last_god_action": "",
    "small_world_last_god_sent_at": 0,
    "small_world_last_disaster_wave_at": 0,
    "small_world_barrier_msg_id": 0,
    "small_world_barrier_due_at": 0,
    "small_world_last_barrier_sent_at": 0,
    "small_world_phase": "idle",
    "small_world_query_msg_id": 0,
    "small_world_manifest_msg_id": 0,
    "small_world_manifest_cost_text": "",
    "small_world_harvest_msg_id": 0,
    "small_world_refine_msg_id": 0,
    "small_world_refresh_count": 0,
    "small_world_pending_incense": 0,
    "small_world_incense_stock": 0,
    "small_world_faith_value": 0,
    "small_world_panel_snapshot": {},
    "small_world_last_panel_at": 0,
    "small_world_last_error": "",
    "resource_shortage_backoffs": {},
    "action_guard_sessions": {},

    # 第二元神模块
    "second_soul_enabled": False,
    "second_soul_auto_choice_enabled": True,
    "second_soul_phase": "idle",  # idle|status_pending|ready_to_train|train_pending|cultivating|heart_demon_pending|injured|not_unlocked|purge_pending|purge_status_pending
    "second_soul_choice_strategy": "stable",
    "next_second_soul_time": 0,
    "second_soul_heart_demon_msg_id": 0,
    "second_soul_heart_demon_deadline": 0,
    "second_soul_heart_demon_notified": False,
    "second_soul_status_msg_id": 0,
    "second_soul_train_msg_id": 0,
    "second_soul_last_train_started_at": 0,
    "second_soul_last_broadcast_key": "",
    "second_soul_last_broadcast_at": 0,
    "second_soul_moran_value": 0,
    "second_soul_purge_msg_id": 0,
    "second_soul_purge_status_msg_id": 0,
    "second_soul_purge_attempts": 0,
    "second_soul_purge_due_at": 0,
    "second_soul_purge_last_at": 0,
    "second_soul_last_error": "",

    # 太一门模块
    "taiyi_enabled": False,
    "taiyi_yindao_element": "水",
    "taiyi_node_search_enabled": False,
    "taiyi_phase": "idle",  # idle|yindao_pending|search_pending|define_pending|frozen
    "taiyi_pending_node_name": "",
    "taiyi_yindao_msg_id": 0,
    "taiyi_node_search_msg_id": 0,
    "taiyi_node_define_msg_id": 0,
    "next_taiyi_cycle_time": 0,
    "taiyi_phase_entered_at": 0,
    "taiyi_freeze_until": 0,
    "taiyi_freeze_reason": "",
    "taiyi_failure_history": [],
    "taiyi_yindao_resend_count": 0,
    "taiyi_search_resend_count": 0,
    "taiyi_last_error": "",

    # 身份级异常状态
    "weak_until": 0,
    "weak_reason": "",
    "weak_source": "",
    "weak_last_block_log_at": 0,

    # 运行态
    "identity_info_reply_msg_ids": [],
    "last_identity_info_msg_id": 0,
    "identity_info_last_error": "",
    "identity_info_last_requested_at": 0,
    "identity_info_followup_due_at": 0,
    "identity_info_primary_payload": {},
    "startup_module_alerts": [],

    # 元婴阻塞日志去重
    "yuanying_waiting_logged": False,
    "yuanying_protect_logged": False,

    # 深度闭关阻塞日志去重
    "deep_retreat_waiting_logged": False,
    "deep_retreat_protect_logged": False,

    # 追踪补发模块（运行态，不持久化）
    "pending_tasks": {},
    # { msg_id: sent_at }
    "my_msg_ids": {},
}

GLOBAL_STATE_DEFAULTS = {
    "my_user_id": None,
    "game_group_id": int(GAME_GROUP_ID),
    "game_bot_ids": sorted(int(bot_id) for bot_id in GAME_BOT_IDS),
    "game_listener_account_ids": [],
    "game_topic_id": int(GAME_TOPIC_ID),
    "forum_topics": [],
    "forum_topics_updated_at": 0,
    "auto_delete_sent_messages": True,
    "global_enabled": True,
    "tiandao_judgement_enabled": False,
    "tiandao_judgement_pending": {},
    "tianji_quiz_pending": {},
    "divination_pending_exchanges": {},
    "divination_run_state": {},
    "world_boss_run_state": {},
    "guanxing_monitor_enabled": False,
    "guanxing_monitor_targets": list(GUANXING_TARGET_KEYWORDS[:2]),
    "guanxing_shift_target": "",
    "guanxing_shift_delay_sec": GUANXING_SHIFT_START_DELAY_SEC,
    "next_guanxing_monitor_notify_time": 0,
    "guanxing_monitor_slot_key": "",
    "guanxing_monitor_slot_start_at": 0,
    "guanxing_monitor_slot_end_at": 0,
    "guanxing_monitor_seen_panel": False,
    "guanxing_monitor_matched_keyword": "",
    "guanxing_monitor_matched_value": "",
    "guanxing_monitor_last_evolution_value": "",
    "guanxing_monitor_last_seen_at": 0,
    "guanxing_monitor_last_notified_slot_key": "",
    "guanxing_round_state": {},
    "formation_run_state": {},
    "replica_group_id": 0,
    "replica_group_ids": [],
    "replica_listener_account_id": 0,
    "replica_listener_account_map": {},
    "replica_dispatch_group_ids": [],
    "replica_dispatch_listener_account_map": {},
    "replica_participant_identity_ids": [],
    "replica_dispatch_participant_identity_ids": [],
    "replica_run_state": {},
    "replica_virtual_hall_match_enabled_map": {},
    "replica_query_aggregator_config": {},
    "replica_success_cooldown_hours": copy.deepcopy(REPLICA_SUCCESS_COOLDOWN_HOUR_DEFAULTS),
    "storage_bag_api_config": {},
    "storage_bag_records": {},
    "storage_bag_item_rules": {},
    "tianjige_dao_path_records": {},
    "dungeon_join_run_state": {},
    "dungeon_quiet_until": 0,
    "dungeon_quiet_reason": "",
    "dungeon_quiet_last_log_at": 0,
    "mulan_intel_state": {},
    "send_as_profiles": {},
    "identity_states": {},
    "identity_ids": [],
    "quiz_learning_watchers": {},
    "quiz_ai_config": {},
    "accounts": {},
    "identity_account_map": {},
    "identity_membership_initialized": False,
    "delayed_actions_state": {},
}
_meta_state = copy.deepcopy(GLOBAL_STATE_DEFAULTS)


def new_identity_state():
    return copy.deepcopy(IDENTITY_STATE_TEMPLATE)


def get_pending_command(pending):
    if not isinstance(pending, dict):
        return ""
    return str(pending.get("cmd") or pending.get("command") or "").strip()


def has_identity(send_as_id):
    try:
        send_as_id = int(send_as_id)
    except (TypeError, ValueError):
        return False
    return send_as_id in _meta_state["identity_ids"]


def ensure_identity_registered(send_as_id):
    send_as_id = int(send_as_id)
    if send_as_id not in _meta_state["identity_ids"]:
        _meta_state["identity_ids"].append(send_as_id)
    if send_as_id not in _meta_state["identity_states"]:
        _meta_state["identity_states"][send_as_id] = new_identity_state()
    return _meta_state["identity_states"][send_as_id]


def remove_identity(send_as_id):
    send_as_id = int(send_as_id)
    removed = False
    if send_as_id in _meta_state["identity_ids"]:
        _meta_state["identity_ids"] = [identity_id for identity_id in _meta_state["identity_ids"] if identity_id != send_as_id]
        removed = True
    if _meta_state["identity_states"].pop(send_as_id, None) is not None:
        removed = True
    if _meta_state["send_as_profiles"].pop(send_as_id, None) is not None:
        removed = True
    identity_account_map = get_identity_account_map()
    if identity_account_map.pop(str(send_as_id), None) is not None:
        set_identity_account_map(identity_account_map)
        removed = True
    if not has_active_identity_context() and int(_current_identity_id.get() or 0) == send_as_id:
        fallback_identity_id = int((_meta_state["identity_ids"] or [0])[0] or 0)
        _current_identity_id.set(fallback_identity_id)
    return removed


def get_identity_ids():
    for send_as_id in list(_meta_state["identity_ids"]):
        if send_as_id not in _meta_state["identity_states"]:
            _meta_state["identity_states"][send_as_id] = new_identity_state()
    return list(_meta_state["identity_ids"])


def get_current_identity_id():
    send_as_id = int(_current_identity_id.get() or 0)
    if has_identity(send_as_id):
        return send_as_id
    identity_ids = get_identity_ids()
    if identity_ids:
        return int(identity_ids[0])
    return int(send_as_id or 0)


def has_active_identity_context():
    return bool(_identity_context_active.get())


def get_active_identity_id():
    if not has_active_identity_context():
        return None
    send_as_id = _current_identity_id.get()
    return int(send_as_id or 0) or None


def get_identity_state(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    send_as_id = int(send_as_id or 0)
    if not has_identity(send_as_id):
        raise KeyError(f"unknown identity: {send_as_id}")
    if send_as_id not in _meta_state["identity_states"]:
        _meta_state["identity_states"][send_as_id] = new_identity_state()
    return _meta_state["identity_states"][send_as_id]


def _coerce_send_as_profile_field(field_name, value):
    if field_name in {"username", "label"}:
        return value or ""
    if field_name in {"daohao", "realm", "spiritual_root_type", "spiritual_root_attrs", "replica_professions", "sect_name", "battle_power_text", "jiyin_choice", "nanlong_choice"}:
        return (value or "").strip()
    if field_name == "stargazer_star_choice":
        normalized = (value or "").strip()
        return normalized if normalized in STARGAZER_STAR_CHOICES else STARGAZER_STAR_CHOICES[0]
    if field_name == "tianti_rank_choice":
        normalized = (value or "").strip()
        return normalized if normalized in TIANTI_RANK_CHOICES else TIANTI_RANK_CHOICES[0]
    if field_name == "pet_name":
        return (value or "").strip() or DEFAULT_PET_NAME
    if field_name == "pet_trial_name":
        return (value or "").strip()
    if field_name in {"sect_updated_at", "sect_contribution_updated_at"}:
        return float(value or 0)
    if field_name in {"xiuwei_current", "xiuwei_max", "battle_power_value", "sect_contribution", "stargazer_total_slots"}:
        return int(value or 0)
    if field_name in {
        "checkin_window_start_hour_utc",
        "checkin_window_end_hour_utc",
        "tower_window_start_hour_utc",
        "tower_window_end_hour_utc",
    }:
        return int(value)
    if field_name in {"enabled", "replica_gold_dps_enabled"}:
        return bool(value)
    return value


def _normalize_send_as_profile_updates(changes):
    normalized = {}
    for field_name, raw_value in (changes or {}).items():
        if raw_value is None:
            continue
        normalized[field_name] = _coerce_send_as_profile_field(field_name, raw_value)
    return normalized


def _normalize_game_bot_ids(bot_ids):
    normalized = []
    seen = set()
    for raw_id in bot_ids or []:
        try:
            bot_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if bot_id in seen:
            continue
        seen.add(bot_id)
        normalized.append(bot_id)
    return normalized


def _normalize_forum_topics(topics):
    normalized = []
    seen_topic_ids = set()
    for item in topics or []:
        if not isinstance(item, dict):
            continue
        try:
            topic_id = int(item.get("id") or 0)
        except (TypeError, ValueError):
            continue
        if topic_id <= 0 or topic_id in seen_topic_ids:
            continue
        seen_topic_ids.add(topic_id)
        normalized.append({
            "id": topic_id,
            "title": str(item.get("title") or "").strip() or f"话题 {topic_id}",
            "top_message": int(item.get("top_message") or 0),
        })
    return normalized


def set_send_as_profile(
    send_as_id,
    username="",
    label="",
    daohao=None,
    realm=None,
    spiritual_root_type=None,
    spiritual_root_attrs=None,
    replica_professions=None,
    replica_gold_dps_enabled=None,
    pet_name=None,
    pet_warm_name=None,
    pet_trial_name=None,
    sect_name=None,
    sect_updated_at=None,
    xiuwei_current=None,
    xiuwei_max=None,
    battle_power_text=None,
    battle_power_value=None,
    jiyin_choice=None,
    nanlong_choice=None,
    stargazer_star_choice=None,
    tianti_rank_choice=None,
    stargazer_total_slots=None,
    checkin_window_start_hour_utc=None,
    checkin_window_end_hour_utc=None,
    tower_window_start_hour_utc=None,
    tower_window_end_hour_utc=None,
    enabled=None,
):
    return update_send_as_profile(
        send_as_id,
        username=username,
        label=label,
        daohao=daohao,
        realm=realm,
        spiritual_root_type=spiritual_root_type,
        spiritual_root_attrs=spiritual_root_attrs,
        replica_professions=replica_professions,
        replica_gold_dps_enabled=replica_gold_dps_enabled,
        pet_name=pet_name,
        pet_warm_name=pet_warm_name,
        pet_trial_name=pet_trial_name,
        sect_name=sect_name,
        sect_updated_at=sect_updated_at,
        xiuwei_current=xiuwei_current,
        xiuwei_max=xiuwei_max,
        battle_power_text=battle_power_text,
        battle_power_value=battle_power_value,
        jiyin_choice=jiyin_choice,
        nanlong_choice=nanlong_choice,
        stargazer_star_choice=stargazer_star_choice,
        tianti_rank_choice=tianti_rank_choice,
        stargazer_total_slots=stargazer_total_slots,
        checkin_window_start_hour_utc=checkin_window_start_hour_utc,
        checkin_window_end_hour_utc=checkin_window_end_hour_utc,
        tower_window_start_hour_utc=tower_window_start_hour_utc,
        tower_window_end_hour_utc=tower_window_end_hour_utc,
        enabled=enabled,
    )


def infer_replica_professions(spiritual_root_attrs):
    attrs_text = str(spiritual_root_attrs or "")
    professions = []
    for profession, attrs in REPLICA_PROFESSION_RULES:
        if any(attr in attrs_text for attr in attrs):
            professions.append(profession)
    return "|".join(professions)


def _profile_allows_replica_gold_dps(profile):
    attrs_text = str((profile or {}).get("spiritual_root_attrs") or "")
    return any(attr in attrs_text for attr in ("金", "雷"))


def _normalize_replica_gold_dps_profile(profile):
    if not _profile_allows_replica_gold_dps(profile):
        profile["replica_gold_dps_enabled"] = False
    else:
        profile["replica_gold_dps_enabled"] = bool(profile.get("replica_gold_dps_enabled", False))
    return profile


def _normalize_replica_professions_profile(profile, *, infer_from_root=False):
    explicit_professions = str((profile or {}).get("replica_professions") or "").strip()
    if explicit_professions and not infer_from_root:
        profile["replica_professions"] = explicit_professions
    else:
        profile["replica_professions"] = infer_replica_professions(profile.get("spiritual_root_attrs") or "")
    return profile


def update_send_as_profile(send_as_id, **changes):
    send_as_id = int(send_as_id)
    ensure_identity_registered(send_as_id)
    profile = dict(SEND_AS_PROFILE_DEFAULTS)
    profile.update(_meta_state["send_as_profiles"].get(send_as_id, {}))
    normalized_changes = _normalize_send_as_profile_updates(changes)
    profile.update(normalized_changes)
    _normalize_replica_professions_profile(
        profile,
        infer_from_root="spiritual_root_attrs" in normalized_changes and "replica_professions" not in normalized_changes,
    )
    _normalize_replica_gold_dps_profile(profile)
    _meta_state["send_as_profiles"][send_as_id] = profile
    return profile


def get_send_as_profile(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    profile = dict(SEND_AS_PROFILE_DEFAULTS)
    profile.update(_meta_state["send_as_profiles"].get(int(send_as_id), {}))
    _normalize_replica_professions_profile(profile)
    _normalize_replica_gold_dps_profile(profile)
    if not (profile.get("realm") or "").strip():
        inferred_realm = infer_realm_from_xiuwei_max(profile.get("xiuwei_max", 0))
        if inferred_realm:
            profile["realm"] = inferred_realm
    return profile


def is_replica_gold_dps_allowed(send_as_id=None):
    return _profile_allows_replica_gold_dps(get_send_as_profile(send_as_id))


def get_replica_gold_dps_enabled(send_as_id=None):
    profile = get_send_as_profile(send_as_id)
    return _profile_allows_replica_gold_dps(profile) and bool(profile.get("replica_gold_dps_enabled", False))


def set_replica_gold_dps_enabled(send_as_id, enabled):
    send_as_id = int(send_as_id)
    profile = get_send_as_profile(send_as_id)
    update_send_as_profile(send_as_id, replica_gold_dps_enabled=bool(enabled) and _profile_allows_replica_gold_dps(profile))
    return get_replica_gold_dps_enabled(send_as_id)


def get_identity_enabled(send_as_id=None):
    return bool(get_send_as_profile(send_as_id).get("enabled", True))


def set_identity_enabled(send_as_id, enabled):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, enabled=bool(enabled))
    return get_identity_enabled(send_as_id)


def get_storage_bag_records():
    records = _meta_state.get("storage_bag_records") or {}
    return records if isinstance(records, dict) else {}


def set_storage_bag_records(records):
    _meta_state["storage_bag_records"] = records if isinstance(records, dict) else {}
    return get_storage_bag_records()


def get_tianjige_dao_path_records():
    records = _meta_state.get("tianjige_dao_path_records") or {}
    return records if isinstance(records, dict) else {}


def set_tianjige_dao_path_records(records):
    _meta_state["tianjige_dao_path_records"] = records if isinstance(records, dict) else {}
    return get_tianjige_dao_path_records()


def get_divination_pending_exchanges():
    records = _meta_state.get("divination_pending_exchanges") or {}
    return records if isinstance(records, dict) else {}


def set_divination_pending_exchanges(records):
    _meta_state["divination_pending_exchanges"] = records if isinstance(records, dict) else {}
    return get_divination_pending_exchanges()


def get_divination_run_state():
    records = _meta_state.get("divination_run_state") or {}
    return records if isinstance(records, dict) else {}


def set_divination_run_state(records):
    _meta_state["divination_run_state"] = records if isinstance(records, dict) else {}
    return get_divination_run_state()


def get_world_boss_run_state():
    records = _meta_state.get("world_boss_run_state") or {}
    return records if isinstance(records, dict) else {}


def set_world_boss_run_state(records):
    _meta_state["world_boss_run_state"] = records if isinstance(records, dict) else {}
    return get_world_boss_run_state()


def normalize_divination_daily_limit(value):
    try:
        limit = int(value)
    except (TypeError, ValueError):
        limit = DIVINATION_DEFAULT_DAILY_LIMIT
    return max(1, min(20, limit))


def get_divination_daily_limit(send_as_id=None):
    return normalize_divination_daily_limit(get_identity_state(send_as_id).get("divination_daily_limit"))


def set_divination_daily_limit(send_as_id, value):
    identity_state = get_identity_state(send_as_id)
    identity_state["divination_daily_limit"] = normalize_divination_daily_limit(value)
    return identity_state["divination_daily_limit"]


def _normalize_storage_bag_api_config(config):
    config = config if isinstance(config, dict) else {}
    base_url = str(config.get("base_url") or "").strip().rstrip("/")
    api_token = str(config.get("api_token") or "").strip()
    cookie = str(config.get("cookie") or "").strip()
    keepalive_enabled = bool(config.get("keepalive_enabled"))
    item_name_map = config.get("item_name_map") if isinstance(config.get("item_name_map"), dict) else {}
    try:
        verified_at = float(config.get("verified_at") or 0)
    except (TypeError, ValueError):
        verified_at = 0
    try:
        last_keepalive_at = float(config.get("last_keepalive_at") or 0)
    except (TypeError, ValueError):
        last_keepalive_at = 0
    try:
        next_keepalive_at = float(config.get("next_keepalive_at") or 0)
    except (TypeError, ValueError):
        next_keepalive_at = 0
    return {
        "base_url": base_url,
        "api_token": api_token,
        "cookie": cookie,
        "item_name_map": {
            str(item_id): str(name)
            for item_id, name in item_name_map.items()
            if str(item_id or "").strip() and str(name or "").strip()
        },
        "keepalive_enabled": keepalive_enabled,
        "verified_at": verified_at,
        "last_keepalive_at": last_keepalive_at,
        "last_keepalive_ok": bool(config.get("last_keepalive_ok")),
        "last_keepalive_error": str(config.get("last_keepalive_error") or "").strip(),
        "next_keepalive_at": next_keepalive_at,
    }


def get_storage_bag_api_config():
    return _normalize_storage_bag_api_config(_meta_state.get("storage_bag_api_config") or {})


def set_storage_bag_api_config(config):
    _meta_state["storage_bag_api_config"] = _normalize_storage_bag_api_config(config)
    return get_storage_bag_api_config()


def is_storage_bag_api_configured():
    config = get_storage_bag_api_config()
    return bool(config.get("cookie"))


def get_storage_bag_item_rules():
    records = _meta_state.get("storage_bag_item_rules") or {}
    return records if isinstance(records, dict) else {}


def set_storage_bag_item_rules(records):
    _meta_state["storage_bag_item_rules"] = records if isinstance(records, dict) else {}
    return get_storage_bag_item_rules()


def get_dungeon_join_run_state():
    records = _meta_state.get("dungeon_join_run_state") or {}
    return records if isinstance(records, dict) else {}


def set_dungeon_join_run_state(records):
    _meta_state["dungeon_join_run_state"] = records if isinstance(records, dict) else {}
    return get_dungeon_join_run_state()


def _get_meta_dict(key):
    value = _meta_state.get(key) or {}
    return value if isinstance(value, dict) else {}


def _set_meta_dict(key, value):
    _meta_state[key] = value if isinstance(value, dict) else {}


def get_replica_group_id():
    group_ids = get_replica_group_ids()
    if group_ids:
        return int(group_ids[0])
    return int(_meta_state.get("replica_group_id") or 0)


def set_replica_group_id(group_id):
    _meta_state["replica_group_id"] = int(group_id or 0)
    if int(group_id or 0):
        set_replica_group_ids([int(group_id or 0)])
    else:
        _meta_state["replica_group_ids"] = []
        _meta_state["replica_listener_account_map"] = {}
        _meta_state["replica_virtual_hall_match_enabled_map"] = {}
    return get_replica_group_id()


def _normalize_replica_group_ids(group_ids):
    if isinstance(group_ids, str):
        candidates = group_ids.replace("，", ",").replace("\n", ",").split(",")
    else:
        candidates = group_ids or []
    normalized = []
    seen = set()
    for raw_id in candidates:
        try:
            group_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if group_id == 0 or group_id in seen:
            continue
        seen.add(group_id)
        normalized.append(group_id)
    return normalized


def get_replica_group_ids():
    group_ids = _normalize_replica_group_ids(_meta_state.get("replica_group_ids") or [])
    if group_ids:
        return group_ids
    legacy_group_id = int(_meta_state.get("replica_group_id") or 0)
    return [legacy_group_id] if legacy_group_id else []


def set_replica_group_ids(group_ids):
    normalized = _normalize_replica_group_ids(group_ids)
    _meta_state["replica_group_ids"] = normalized
    _meta_state["replica_group_id"] = int(normalized[0]) if normalized else 0
    get_replica_virtual_hall_match_enabled_map()
    _meta_state["replica_dispatch_group_ids"] = _normalize_replica_dispatch_group_ids(
        _meta_state.get("replica_dispatch_group_ids") or []
    )
    _meta_state["replica_dispatch_listener_account_map"] = _normalize_replica_dispatch_listener_account_map(
        _meta_state.get("replica_dispatch_listener_account_map") or {}
    )
    return get_replica_group_ids()


def get_replica_listener_account_id():
    listener_map = get_replica_listener_account_map()
    group_id = get_replica_group_id()
    if group_id and str(group_id) in listener_map:
        return int(listener_map.get(str(group_id)) or 0)
    return int(_meta_state.get("replica_listener_account_id") or 0)


def set_replica_listener_account_id(account_id):
    _meta_state["replica_listener_account_id"] = int(account_id or 0)
    group_id = get_replica_group_id()
    if group_id and int(account_id or 0):
        listener_map = get_replica_listener_account_map()
        listener_map[str(group_id)] = int(account_id or 0)
        set_replica_listener_account_map(listener_map)
    return get_replica_listener_account_id()


def _normalize_replica_listener_account_map(listener_map):
    normalized = {}
    group_ids = set(get_replica_group_ids())
    for raw_group_id, raw_account_id in (listener_map or {}).items():
        try:
            group_id = int(raw_group_id)
            account_id = int(raw_account_id)
        except (TypeError, ValueError):
            continue
        if group_id == 0 or account_id <= 0:
            continue
        if group_ids and group_id not in group_ids:
            continue
        normalized[str(group_id)] = account_id
    return normalized


def get_replica_listener_account_map():
    listener_map = _normalize_replica_listener_account_map(_meta_state.get("replica_listener_account_map") or {})
    if listener_map:
        return listener_map
    legacy_group_id = int(_meta_state.get("replica_group_id") or 0)
    legacy_account_id = int(_meta_state.get("replica_listener_account_id") or 0)
    if legacy_group_id and legacy_account_id:
        return {str(legacy_group_id): legacy_account_id}
    return {}


def set_replica_listener_account_map(listener_map):
    normalized = _normalize_replica_listener_account_map(listener_map)
    _meta_state["replica_listener_account_map"] = normalized
    group_id = get_replica_group_id()
    _meta_state["replica_listener_account_id"] = int(normalized.get(str(group_id)) or 0) if group_id else 0
    return get_replica_listener_account_map()


def _normalize_replica_dispatch_group_ids(group_ids):
    normalized = _normalize_replica_group_ids(group_ids)
    reserved_group_ids = set(get_replica_group_ids())
    game_group_id = get_game_group_id()
    if game_group_id:
        reserved_group_ids.add(game_group_id)
    return [group_id for group_id in normalized if group_id not in reserved_group_ids]


def get_replica_dispatch_group_ids():
    return _normalize_replica_dispatch_group_ids(_meta_state.get("replica_dispatch_group_ids") or [])


def set_replica_dispatch_group_ids(group_ids):
    normalized = _normalize_replica_dispatch_group_ids(group_ids)
    _meta_state["replica_dispatch_group_ids"] = normalized
    _meta_state["replica_dispatch_listener_account_map"] = _normalize_replica_dispatch_listener_account_map(
        _meta_state.get("replica_dispatch_listener_account_map") or {}
    )
    return get_replica_dispatch_group_ids()


def _normalize_replica_dispatch_listener_account_map(listener_map):
    normalized = {}
    group_ids = set(get_replica_dispatch_group_ids())
    for raw_group_id, raw_account_id in (listener_map or {}).items():
        try:
            group_id = int(raw_group_id)
            account_id = int(raw_account_id)
        except (TypeError, ValueError):
            continue
        if group_id == 0 or account_id <= 0:
            continue
        if group_ids and group_id not in group_ids:
            continue
        normalized[str(group_id)] = account_id
    return normalized


def get_replica_dispatch_listener_account_map():
    return _normalize_replica_dispatch_listener_account_map(
        _meta_state.get("replica_dispatch_listener_account_map") or {}
    )


def set_replica_dispatch_listener_account_map(listener_map):
    _meta_state["replica_dispatch_listener_account_map"] = _normalize_replica_dispatch_listener_account_map(listener_map)
    return get_replica_dispatch_listener_account_map()


def _normalize_replica_participant_identity_ids(identity_ids):
    normalized = []
    seen = set()
    known_ids = set(get_identity_ids())
    for raw_id in identity_ids or []:
        try:
            identity_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if identity_id <= 0 or identity_id in seen or identity_id not in known_ids:
            continue
        seen.add(identity_id)
        normalized.append(identity_id)
    return normalized


def get_replica_participant_identity_ids():
    return _normalize_replica_participant_identity_ids(_meta_state.get("replica_participant_identity_ids") or [])


def set_replica_participant_identity_ids(identity_ids):
    _meta_state["replica_participant_identity_ids"] = _normalize_replica_participant_identity_ids(identity_ids)
    return get_replica_participant_identity_ids()


def get_replica_dispatch_participant_identity_ids():
    return _normalize_replica_participant_identity_ids(_meta_state.get("replica_dispatch_participant_identity_ids") or [])


def set_replica_dispatch_participant_identity_ids(identity_ids):
    _meta_state["replica_dispatch_participant_identity_ids"] = _normalize_replica_participant_identity_ids(identity_ids)
    return get_replica_dispatch_participant_identity_ids()


def get_replica_run_state():
    return _get_meta_dict("replica_run_state")


def set_replica_run_state(records):
    _set_meta_dict("replica_run_state", records)
    return get_replica_run_state()


def _coerce_meta_bool(value, default=False):
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().casefold()
    if text in {"1", "true", "yes", "y", "on", "open", "enable", "enabled", "开", "开启", "启用"}:
        return True
    if text in {"", "0", "false", "no", "n", "off", "close", "disable", "disabled", "关", "关闭", "禁用"}:
        return False
    return bool(default)


def _normalize_replica_virtual_hall_match_enabled_map(enabled_map):
    normalized = {}
    group_ids = set(get_replica_group_ids())
    if not group_ids:
        return normalized
    for raw_group_id, raw_enabled in (enabled_map or {}).items():
        try:
            group_id = int(raw_group_id)
        except (TypeError, ValueError):
            continue
        if group_id == 0 or group_id not in group_ids:
            continue
        normalized[str(group_id)] = _coerce_meta_bool(raw_enabled)
    return normalized


def get_replica_virtual_hall_match_enabled_map():
    enabled_map = _normalize_replica_virtual_hall_match_enabled_map(_meta_state.get("replica_virtual_hall_match_enabled_map") or {})
    _meta_state["replica_virtual_hall_match_enabled_map"] = enabled_map
    return enabled_map


def set_replica_virtual_hall_match_enabled_map(enabled_map):
    _meta_state["replica_virtual_hall_match_enabled_map"] = _normalize_replica_virtual_hall_match_enabled_map(enabled_map)
    return get_replica_virtual_hall_match_enabled_map()


def set_replica_virtual_hall_match_enabled(group_id, enabled):
    try:
        group_id = int(group_id)
    except (TypeError, ValueError):
        return False
    if group_id == 0 or group_id not in set(get_replica_group_ids()):
        return False
    enabled_map = get_replica_virtual_hall_match_enabled_map()
    enabled_map[str(group_id)] = _coerce_meta_bool(enabled)
    set_replica_virtual_hall_match_enabled_map(enabled_map)
    return is_replica_virtual_hall_match_enabled(group_id)


def is_replica_virtual_hall_match_enabled(group_id):
    try:
        group_id = int(group_id)
    except (TypeError, ValueError):
        return False
    return bool(get_replica_virtual_hall_match_enabled_map().get(str(group_id), False))


def _normalize_replica_query_aggregator_config(config):
    config = config if isinstance(config, dict) else {}
    base_url = str(config.get("base_url") or "").strip().rstrip("/")
    client_id = str(config.get("client_id") or "").strip()
    secret = str(config.get("secret") or "").strip()
    return {
        "base_url": base_url,
        "client_id": client_id,
        "secret": secret,
    }


def get_replica_query_aggregator_config():
    return _normalize_replica_query_aggregator_config(_meta_state.get("replica_query_aggregator_config") or {})


def set_replica_query_aggregator_config(config):
    _meta_state["replica_query_aggregator_config"] = _normalize_replica_query_aggregator_config(config)
    return get_replica_query_aggregator_config()


def is_replica_query_aggregator_configured():
    config = get_replica_query_aggregator_config()
    return bool(config.get("base_url") and config.get("client_id") and config.get("secret"))


def _normalize_replica_success_cooldown_hours(config):
    source = config if isinstance(config, dict) else {}
    normalized = copy.deepcopy(REPLICA_SUCCESS_COOLDOWN_HOUR_DEFAULTS)
    for kind in REPLICA_SUCCESS_COOLDOWN_HOUR_DEFAULTS:
        try:
            hours = float(source.get(kind, normalized[kind]))
        except (TypeError, ValueError):
            hours = float(normalized[kind])
        normalized[kind] = round(max(0.25, min(24.0, hours)), 2)
    return normalized


def get_replica_success_cooldown_hours():
    normalized = _normalize_replica_success_cooldown_hours(_meta_state.get("replica_success_cooldown_hours") or {})
    _meta_state["replica_success_cooldown_hours"] = normalized
    return copy.deepcopy(normalized)


def set_replica_success_cooldown_hours(config):
    _meta_state["replica_success_cooldown_hours"] = _normalize_replica_success_cooldown_hours(config)
    return get_replica_success_cooldown_hours()


def get_send_as_label(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    profile = get_send_as_profile(send_as_id)
    return profile.get("username") or profile.get("label") or str(send_as_id)


def get_pet_name(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    return get_send_as_profile(send_as_id).get("pet_name") or DEFAULT_PET_NAME


def get_pet_trial_name(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    profile = get_send_as_profile(send_as_id)
    return profile.get("pet_trial_name") or profile.get("pet_name") or DEFAULT_PET_NAME


def get_pet_warm_name(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    profile = get_send_as_profile(send_as_id)
    return profile.get("pet_warm_name") or profile.get("pet_name") or DEFAULT_PET_NAME


def get_jiyin_choice(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    return (get_send_as_profile(send_as_id).get("jiyin_choice") or "").strip()


def set_jiyin_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, jiyin_choice=choice)
    return get_jiyin_choice(send_as_id)


def get_nanlong_choice(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    return (get_send_as_profile(send_as_id).get("nanlong_choice") or "reject").strip() or "reject"


def set_nanlong_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, nanlong_choice=choice or "reject")
    return get_nanlong_choice(send_as_id)


def get_stargazer_star_choice(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    choice = (get_send_as_profile(send_as_id).get("stargazer_star_choice") or "").strip()
    return choice if choice in STARGAZER_STAR_CHOICES else STARGAZER_STAR_CHOICES[0]


def set_stargazer_star_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, stargazer_star_choice=choice)
    return get_stargazer_star_choice(send_as_id)


def get_stargazer_total_slots(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    return int(get_send_as_profile(send_as_id).get("stargazer_total_slots", 0) or 0)


def set_stargazer_total_slots(send_as_id, total_slots):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, stargazer_total_slots=max(0, int(total_slots or 0)))
    return get_stargazer_total_slots(send_as_id)


def get_tianti_rank_choice(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    choice = (get_send_as_profile(send_as_id).get("tianti_rank_choice") or "").strip()
    return choice if choice in TIANTI_RANK_CHOICES else TIANTI_RANK_CHOICES[0]


def set_tianti_rank_choice(send_as_id, choice):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, tianti_rank_choice=choice)
    return get_tianti_rank_choice(send_as_id)


def get_game_group_id():
    return int(_meta_state.get("game_group_id") or 0)


def set_game_group_id(group_id):
    _meta_state["game_group_id"] = int(group_id or 0)
    _meta_state["replica_dispatch_group_ids"] = _normalize_replica_dispatch_group_ids(
        _meta_state.get("replica_dispatch_group_ids") or []
    )
    _meta_state["replica_dispatch_listener_account_map"] = _normalize_replica_dispatch_listener_account_map(
        _meta_state.get("replica_dispatch_listener_account_map") or {}
    )
    return get_game_group_id()


def _normalize_game_listener_account_ids(account_ids):
    if isinstance(account_ids, str):
        candidates = account_ids.replace("，", ",").replace("\n", ",").split(",")
    else:
        candidates = account_ids or []
    normalized = []
    seen = set()
    for raw_id in candidates:
        try:
            account_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if account_id <= 0 or account_id in seen:
            continue
        seen.add(account_id)
        normalized.append(account_id)
    return normalized


def get_game_listener_account_ids():
    normalized = _normalize_game_listener_account_ids(_meta_state.get("game_listener_account_ids") or [])
    _meta_state["game_listener_account_ids"] = normalized
    return list(normalized)


def set_game_listener_account_ids(account_ids):
    _meta_state["game_listener_account_ids"] = _normalize_game_listener_account_ids(account_ids)
    return get_game_listener_account_ids()


def get_game_bot_ids():
    return _normalize_game_bot_ids(_meta_state.get("game_bot_ids") or [])


def set_game_bot_ids(bot_ids):
    _meta_state["game_bot_ids"] = sorted(_normalize_game_bot_ids(bot_ids))
    return get_game_bot_ids()


def get_game_topic_id():
    return int(_meta_state.get("game_topic_id") or 0)


def set_game_topic_id(topic_id):
    _meta_state["game_topic_id"] = int(topic_id or 0)
    return get_game_topic_id()


def get_tiandao_judgement_enabled():
    return bool(_meta_state.get("tiandao_judgement_enabled", False))


def set_tiandao_judgement_enabled(enabled):
    _meta_state["tiandao_judgement_enabled"] = bool(enabled)
    return get_tiandao_judgement_enabled()


def get_guanxing_monitor_enabled():
    return bool(_meta_state.get("guanxing_monitor_enabled", False))


def set_guanxing_monitor_enabled(enabled):
    _meta_state["guanxing_monitor_enabled"] = bool(enabled)
    return get_guanxing_monitor_enabled()


def get_guanxing_monitor_target_options():
    return list(GUANXING_TARGET_KEYWORDS)


def _normalize_guanxing_monitor_targets(targets):
    if targets is None:
        return []
    if isinstance(targets, str):
        candidates = [targets]
    else:
        try:
            candidates = list(targets)
        except TypeError:
            candidates = []
    normalized = []
    for target in candidates:
        target_text = str(target or "").strip()
        if target_text in GUANXING_TARGET_KEYWORDS and target_text not in normalized:
            normalized.append(target_text)
    return normalized


def get_guanxing_monitor_targets():
    return _normalize_guanxing_monitor_targets(_meta_state.get("guanxing_monitor_targets"))


def set_guanxing_monitor_targets(targets):
    _meta_state["guanxing_monitor_targets"] = _normalize_guanxing_monitor_targets(targets)
    return get_guanxing_monitor_targets()


def _normalize_guanxing_shift_target(value):
    target = str(value or "").strip()
    if not target:
        return ""
    if not target.startswith("@"):
        target = f"@{target}"
    return target if len(target) > 1 else ""


def get_guanxing_shift_target():
    return _normalize_guanxing_shift_target(_meta_state.get("guanxing_shift_target"))


def set_guanxing_shift_target(target):
    _meta_state["guanxing_shift_target"] = _normalize_guanxing_shift_target(target)
    return get_guanxing_shift_target()


def _normalize_guanxing_shift_delay_sec(value):
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = GUANXING_SHIFT_START_DELAY_SEC
    return max(-180, parsed)


def get_guanxing_shift_delay_sec():
    return _normalize_guanxing_shift_delay_sec(_meta_state.get("guanxing_shift_delay_sec", GUANXING_SHIFT_START_DELAY_SEC))


def set_guanxing_shift_delay_sec(value):
    if value is None:
        value = GUANXING_SHIFT_START_DELAY_SEC
    _meta_state["guanxing_shift_delay_sec"] = _normalize_guanxing_shift_delay_sec(value)
    return get_guanxing_shift_delay_sec()


def get_forum_topics():
    return _normalize_forum_topics(_meta_state.get("forum_topics") or [])


def set_forum_topics(topics, updated_at=None):
    normalized = _normalize_forum_topics(topics)
    normalized.sort(key=lambda item: item["id"])
    _meta_state["forum_topics"] = normalized
    if updated_at is not None:
        _meta_state["forum_topics_updated_at"] = float(updated_at or 0)
    return get_forum_topics()


def get_forum_topics_updated_at():
    return float(_meta_state.get("forum_topics_updated_at") or 0)


def is_auto_delete_sent_messages_enabled():
    return bool(_meta_state.get("auto_delete_sent_messages", True))


def set_auto_delete_sent_messages(enabled):
    _meta_state["auto_delete_sent_messages"] = bool(enabled)
    return is_auto_delete_sent_messages_enabled()


def get_global_enabled():
    return bool(_meta_state.get("global_enabled", True))


def set_global_enabled(enabled):
    _meta_state["global_enabled"] = bool(enabled)
    return get_global_enabled()


def get_guanxing_round_state():
    value = _meta_state.get("guanxing_round_state") or {}
    return value if isinstance(value, dict) else {}


def set_guanxing_round_state(round_state):
    _meta_state["guanxing_round_state"] = round_state if isinstance(round_state, dict) else {}
    return get_guanxing_round_state()


def get_formation_run_state():
    value = _meta_state.get("formation_run_state") or {}
    return value if isinstance(value, dict) else {}


def set_formation_run_state(records):
    _meta_state["formation_run_state"] = records if isinstance(records, dict) else {}
    return get_formation_run_state()


def get_quiz_learning_watchers():
    watchers = _meta_state.get("quiz_learning_watchers") or {}
    return watchers if isinstance(watchers, dict) else {}


def _normalize_quiz_ai_provider(provider_config, index=0, previous=None):
    provider_config = provider_config if isinstance(provider_config, dict) else {}
    previous = previous if isinstance(previous, dict) else {}
    provider = str(provider_config.get("provider") or previous.get("provider") or QUIZ_AI_CONFIG_DEFAULTS["provider"]).strip().lower()
    if provider not in {"codex", "openai", "claude", "anthropic"}:
        provider = QUIZ_AI_CONFIG_DEFAULTS["provider"]
    provider = "claude" if provider in {"claude", "anthropic"} else "codex"
    try:
        timeout_sec = int(provider_config.get("timeout_sec", previous.get("timeout_sec", QUIZ_AI_CONFIG_DEFAULTS["timeout_sec"])))
    except (TypeError, ValueError):
        timeout_sec = QUIZ_AI_CONFIG_DEFAULTS["timeout_sec"]
    try:
        temperature = float(provider_config.get("temperature", previous.get("temperature", QUIZ_AI_CONFIG_DEFAULTS["temperature"])))
    except (TypeError, ValueError):
        temperature = QUIZ_AI_CONFIG_DEFAULTS["temperature"]
    provider_id = str(provider_config.get("id") or previous.get("id") or f"ai{int(index or 0) + 1}").strip()
    label = str(provider_config.get("label") or previous.get("label") or provider_id).strip()
    return {
        "id": provider_id,
        "enabled": bool(provider_config.get("enabled", previous.get("enabled", True))),
        "label": label,
        "provider": provider,
        "base_url": str(provider_config.get("base_url") or previous.get("base_url") or "").strip().rstrip("/"),
        "model": str(provider_config.get("model") or previous.get("model") or "").strip(),
        "api_key": str(provider_config.get("api_key") or previous.get("api_key") or "").strip(),
        "timeout_sec": max(2, min(60, timeout_sec)),
        "temperature": max(0.0, min(2.0, temperature)),
    }


def _normalize_quiz_ai_config(config):
    config = config if isinstance(config, dict) else {}
    try:
        confidence_threshold = float(
            config.get("confidence_threshold", QUIZ_AI_CONFIG_DEFAULTS["confidence_threshold"])
        )
    except (TypeError, ValueError):
        confidence_threshold = QUIZ_AI_CONFIG_DEFAULTS["confidence_threshold"]
    try:
        timeout_sec = int(config.get("timeout_sec", QUIZ_AI_CONFIG_DEFAULTS["timeout_sec"]))
    except (TypeError, ValueError):
        timeout_sec = QUIZ_AI_CONFIG_DEFAULTS["timeout_sec"]
    try:
        decision_timeout_sec = float(config.get("decision_timeout_sec", QUIZ_AI_CONFIG_DEFAULTS["decision_timeout_sec"]))
    except (TypeError, ValueError):
        decision_timeout_sec = QUIZ_AI_CONFIG_DEFAULTS["decision_timeout_sec"]
    try:
        answer_safety_margin_sec = float(config.get("answer_safety_margin_sec", QUIZ_AI_CONFIG_DEFAULTS["answer_safety_margin_sec"]))
    except (TypeError, ValueError):
        answer_safety_margin_sec = QUIZ_AI_CONFIG_DEFAULTS["answer_safety_margin_sec"]
    try:
        temperature = float(config.get("temperature", QUIZ_AI_CONFIG_DEFAULTS["temperature"]))
    except (TypeError, ValueError):
        temperature = QUIZ_AI_CONFIG_DEFAULTS["temperature"]
    try:
        last_confidence = float(config.get("last_confidence") or 0)
    except (TypeError, ValueError):
        last_confidence = 0
    try:
        last_updated_at = float(config.get("last_updated_at") or 0)
    except (TypeError, ValueError):
        last_updated_at = 0
    try:
        last_provider_count = int(config.get("last_provider_count") or 0)
    except (TypeError, ValueError):
        last_provider_count = 0
    try:
        last_valid_count = int(config.get("last_valid_count") or 0)
    except (TypeError, ValueError):
        last_valid_count = 0
    try:
        last_decision_timeout_sec = float(config.get("last_decision_timeout_sec") or 0)
    except (TypeError, ValueError):
        last_decision_timeout_sec = 0
    last_answer = str(config.get("last_answer") or "").strip().upper()
    if last_answer not in {"A", "B", "C", "D"}:
        last_answer = ""
    raw_providers = config.get("providers") if isinstance(config.get("providers"), list) else []
    providers = []
    seen_provider_ids = set()
    if raw_providers:
        for index, raw_provider in enumerate(raw_providers[:6]):
            item = _normalize_quiz_ai_provider(raw_provider, index)
            if not item["id"] or item["id"] in seen_provider_ids:
                item["id"] = f"ai{index + 1}"
            seen_provider_ids.add(item["id"])
            if item.get("model") or item.get("base_url") or item.get("api_key") or item.get("label"):
                providers.append(item)
    if not providers and any(str(config.get(key) or "").strip() for key in ("provider", "base_url", "model", "api_key")):
        providers.append(_normalize_quiz_ai_provider({
            "id": "ai1",
            "enabled": True,
            "label": "AI 1",
            "provider": config.get("provider"),
            "base_url": config.get("base_url"),
            "model": config.get("model"),
            "api_key": config.get("api_key"),
            "timeout_sec": timeout_sec,
            "temperature": temperature,
        }, 0))
    first_provider = providers[0] if providers else {}
    raw_last_results = config.get("last_results") if isinstance(config.get("last_results"), list) else []
    last_results = []
    for item in raw_last_results[:6]:
        if not isinstance(item, dict):
            continue
        answer = str(item.get("answer") or "").strip().upper()
        try:
            item_confidence = float(item.get("confidence") or 0)
        except (TypeError, ValueError):
            item_confidence = 0
        try:
            item_elapsed_ms = int(item.get("elapsed_ms") or 0)
        except (TypeError, ValueError):
            item_elapsed_ms = 0
        last_results.append({
            "id": str(item.get("id") or "").strip(),
            "label": str(item.get("label") or item.get("provider") or "").strip(),
            "provider": str(item.get("provider") or "").strip(),
            "ok": bool(item.get("ok")) and answer in {"A", "B", "C", "D"},
            "answer": answer if answer in {"A", "B", "C", "D"} else "",
            "confidence": max(0.0, min(1.0, item_confidence)),
            "elapsed_ms": max(0, item_elapsed_ms),
            "error": str(item.get("error") or "").strip(),
        })
    return {
        "enabled": bool(config.get("enabled")),
        "auto_answer_enabled": bool(config.get("auto_answer_enabled")),
        "provider": first_provider.get("provider") or QUIZ_AI_CONFIG_DEFAULTS["provider"],
        "base_url": first_provider.get("base_url") or "",
        "model": first_provider.get("model") or "",
        "api_key": first_provider.get("api_key") or "",
        "confidence_threshold": max(0.0, min(1.0, confidence_threshold)),
        "timeout_sec": max(3, min(120, timeout_sec)),
        "decision_timeout_sec": max(1.0, min(60.0, decision_timeout_sec)),
        "answer_safety_margin_sec": max(3.0, min(60.0, answer_safety_margin_sec)),
        "temperature": max(0.0, min(2.0, temperature)),
        "providers": providers,
        "last_question": str(config.get("last_question") or "").strip(),
        "last_answer": last_answer,
        "last_confidence": max(0.0, min(1.0, last_confidence)),
        "last_reason": str(config.get("last_reason") or "").strip(),
        "last_error": str(config.get("last_error") or "").strip(),
        "last_provider": str(config.get("last_provider") or "").strip(),
        "last_results": last_results,
        "last_vote_summary": str(config.get("last_vote_summary") or "").strip(),
        "last_provider_count": max(0, last_provider_count),
        "last_valid_count": max(0, last_valid_count),
        "last_decision_timeout_sec": max(0.0, min(60.0, last_decision_timeout_sec)),
        "last_updated_at": last_updated_at,
    }


def get_quiz_ai_config():
    return _normalize_quiz_ai_config(_meta_state.get("quiz_ai_config") or {})


def set_quiz_ai_config(config):
    _meta_state["quiz_ai_config"] = _normalize_quiz_ai_config(config)
    return get_quiz_ai_config()


def set_quiz_learning_watchers(watchers):
    normalized = {}
    for raw_key, item in (watchers or {}).items():
        watcher_key = str(raw_key or "").strip().lower()
        if not watcher_key or not isinstance(item, dict):
            continue
        options = item.get("options") or {}
        normalized_options = {
            option_key: str(options.get(option_key) or "").strip()
            for option_key in ("A", "B", "C", "D")
        }
        matched_answer = str(item.get("matched_answer") or "").strip().upper()
        raw_identity_id = item.get("identity_id")
        try:
            identity_id = int(raw_identity_id or 0) or None
        except (TypeError, ValueError):
            identity_id = None
        normalized[watcher_key] = {
            "target_tag": str(item.get("target_tag") or "").strip(),
            "identity_id": identity_id,
            "question": str(item.get("question") or "").strip(),
            "options": normalized_options,
            "expire_at": float(item.get("expire_at") or 0),
            "matched_answer": matched_answer if matched_answer in {"A", "B", "C", "D"} else "",
        }
    _meta_state["quiz_learning_watchers"] = normalized
    return get_quiz_learning_watchers()


# ================= 多账号管理 =================

def get_accounts():
    accounts = _meta_state.get("accounts") or {}
    return accounts if isinstance(accounts, dict) else {}


def set_accounts(accounts):
    _meta_state["accounts"] = dict(accounts or {})


def get_account(account_id):
    return get_accounts().get(str(account_id))


def set_account(account_id, info):
    accounts = get_accounts()
    accounts[str(account_id)] = info
    set_accounts(accounts)


def get_identity_account_map():
    m = _meta_state.get("identity_account_map") or {}
    return m if isinstance(m, dict) else {}


def set_identity_account_map(m):
    _meta_state["identity_account_map"] = dict(m or {})


def get_identity_account(send_as_id):
    m = get_identity_account_map()
    return int(m.get(str(send_as_id), 0) or 0)


def set_identity_account(send_as_id, account_id):
    send_as_id = int(send_as_id)
    ensure_identity_registered(send_as_id)
    m = get_identity_account_map()
    m[str(send_as_id)] = int(account_id)
    set_identity_account_map(m)


def get_module_window_profile_keys(module_name):
    module_name = (module_name or "").strip()
    if module_name == "点卯":
        return "checkin_window_start_hour_utc", "checkin_window_end_hour_utc"
    if module_name == "闯塔":
        return "tower_window_start_hour_utc", "tower_window_end_hour_utc"
    raise ValueError(f"不支持窗口设置的模块: {module_name}")


def get_module_window_hours(module_name, send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    profile = get_send_as_profile(send_as_id)
    start_key, end_key = get_module_window_profile_keys(module_name)
    start_hour = int(profile.get(start_key, SEND_AS_PROFILE_DEFAULTS[start_key]))
    end_hour = int(profile.get(end_key, SEND_AS_PROFILE_DEFAULTS[end_key]))
    return start_hour, end_hour


def _get_local_offset_hours():
    return int(TZ_LOCAL.utcoffset(None).total_seconds() // 3600)


def get_module_window_hours_local(module_name, send_as_id=None):
    start_hour_utc, end_hour_utc = get_module_window_hours(module_name, send_as_id)
    offset_hours = _get_local_offset_hours()
    return (start_hour_utc + offset_hours) % 24, (end_hour_utc + offset_hours) % 24


def convert_window_hours_local_to_utc(start_hour_local, end_hour_local):
    offset_hours = _get_local_offset_hours()
    return (int(start_hour_local) - offset_hours) % 24, (int(end_hour_local) - offset_hours) % 24


def format_window_text(module_name, send_as_id=None):
    start_hour_utc, end_hour_utc = get_module_window_hours(module_name, send_as_id)
    start_hour_local, end_hour_local = get_module_window_hours_local(module_name, send_as_id)
    return (
        f"UTC+0 {start_hour_utc:02d}:00-{end_hour_utc:02d}:00"
        f"（UTC+8 {start_hour_local:02d}:00-{end_hour_local:02d}:00）"
    )


def set_module_window_hours(module_name, send_as_id, start_hour_utc, end_hour_utc):
    send_as_id = int(send_as_id)
    start_hour_utc = int(start_hour_utc)
    end_hour_utc = int(end_hour_utc)
    if not (0 <= start_hour_utc <= 23 and 0 <= end_hour_utc <= 23):
        raise ValueError("窗口时间必须在 0-23 之间")
    if start_hour_utc >= end_hour_utc:
        raise ValueError("窗口开始时间必须小于结束时间，暂不支持跨天")
    start_key, end_key = get_module_window_profile_keys(module_name)
    update_send_as_profile(
        send_as_id,
        **{
            start_key: start_hour_utc,
            end_key: end_hour_utc,
        },
    )


def get_realm_sort_index(realm, xiuwei_max=0):
    realm = (realm or "").strip() or infer_realm_from_xiuwei_max(xiuwei_max)
    return REALM_SORT_INDEX.get(realm, len(REALM_SORT_INDEX))


def get_realm_sort_key(realm, send_as_id=0, xiuwei_max=0, xiuwei_current=0):
    index = get_realm_sort_index(realm, xiuwei_max=xiuwei_max)
    is_unknown = index >= len(REALM_SORT_ORDER)
    # 按境界从高到低排序；同境界按修为从高到低；未知排最后
    return (1 if is_unknown else 0, -index, -int(xiuwei_current or 0), int(send_as_id or 0))


def is_yuanying_realm_available(send_as_id=None):
    profile = get_send_as_profile(send_as_id)
    realm = (profile.get("realm") or "").strip() or infer_realm_from_xiuwei_max(profile.get("xiuwei_max", 0))
    if not realm:
        return True
    realm_index = REALM_SORT_INDEX.get(realm)
    if realm_index is None:
        return True
    return realm_index >= YUANYING_MIN_REALM_INDEX


def is_explore_rift_realm_available(send_as_id=None):
    return is_yuanying_realm_available(send_as_id)


def is_small_world_realm_available(send_as_id=None):
    profile = get_send_as_profile(send_as_id)
    realm = (profile.get("realm") or "").strip() or infer_realm_from_xiuwei_max(profile.get("xiuwei_max", 0))
    if not realm:
        return False
    realm_index = REALM_SORT_INDEX.get(realm)
    if realm_index is None:
        return False
    return realm_index >= SMALL_WORLD_MIN_REALM_INDEX


def normalize_sect_name(sect_name):
    normalized = str(sect_name or "").strip()
    while normalized.startswith("【") and normalized.endswith("】") and len(normalized) >= 2:
        normalized = normalized[1:-1].strip()
    return normalized


def has_sect_membership(send_as_id=None):
    sect_name = normalize_sect_name(get_send_as_profile(send_as_id).get("sect_name"))
    return sect_name not in NO_SECT_NAMES


def get_available_module_names(send_as_id=None):
    available_module_names = [
        module_name
        for module_name in MODULE_NAMES
        if module_name != "观星监控" and not is_module_archived(module_name)
    ]
    sect_name = normalize_sect_name(get_send_as_profile(send_as_id).get("sect_name"))
    if sect_name in NO_SECT_NAMES:
        available_module_names = [
            module_name for module_name in available_module_names
            if module_name not in GENERIC_SECT_MODULE_NAMES
        ]
    for module_name, required_sect_name in SECT_MODULE_REQUIREMENTS.items():
        if sect_name != required_sect_name:
            available_module_names = [
                available_module_name for available_module_name in available_module_names
                if available_module_name != module_name
            ]
    if not is_yuanying_realm_available(send_as_id):
        available_module_names = [module_name for module_name in available_module_names if module_name != "元婴"]
    if not is_explore_rift_realm_available(send_as_id):
        available_module_names = [module_name for module_name in available_module_names if module_name != "探寻裂缝"]
    if not is_small_world_realm_available(send_as_id):
        available_module_names = [module_name for module_name in available_module_names if module_name != "小世界"]
    return available_module_names


def normalize_wild_training_strategy(strategy):
    normalized = str(strategy or "").strip()
    return normalized if normalized in WILD_TRAINING_STRATEGIES else "谨慎"


def get_wild_training_strategy(send_as_id=None):
    return normalize_wild_training_strategy(get_identity_state(send_as_id).get("wild_training_strategy"))


def set_wild_training_strategy(send_as_id, strategy):
    identity_state = get_identity_state(send_as_id)
    identity_state["wild_training_strategy"] = normalize_wild_training_strategy(strategy)
    return identity_state["wild_training_strategy"]


def is_module_available(module_name, send_as_id=None):
    return module_name in get_available_module_names(send_as_id)


def get_pet_command(send_as_id=None):
    return f".抚摸法宝 {get_pet_name(send_as_id)}"


def get_pet_warm_command(send_as_id=None):
    return f"{CMD_PET_WARM} {get_pet_warm_name(send_as_id)}"


def get_pet_trial_command(send_as_id=None):
    return f"{CMD_PET_TRIAL} {get_pet_trial_name(send_as_id)}"


def get_pet_formation_command(send_as_id=None):
    return CMD_PET_FORMATION


def set_pet_name(send_as_id, pet_name):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, pet_name=pet_name)


def set_pet_warm_name(send_as_id, pet_warm_name):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, pet_warm_name=pet_warm_name)


def set_pet_trial_name(send_as_id, pet_trial_name):
    send_as_id = int(send_as_id)
    update_send_as_profile(send_as_id, pet_trial_name=pet_trial_name)


def get_send_as_tags(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    send_as_id = int(send_as_id)
    profile = get_send_as_profile(send_as_id)

    raw_candidates = [
        profile.get("username", ""),
        profile.get("label", ""),
        profile.get("daohao", ""),
        str(send_as_id),
    ]

    tags = []
    seen = set()
    for candidate in raw_candidates:
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        candidate = candidate.lstrip("@")
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        tags.append(f"@{candidate}")
    return tags


def get_identity_ui_display_name(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    send_as_id = int(send_as_id)
    profile = get_send_as_profile(send_as_id)
    role_name = profile.get("label") or profile.get("username") or str(send_as_id)
    daohao = profile.get("daohao") or str(send_as_id)
    return f"{role_name}[{daohao}]"


def get_identity_display_name(send_as_id=None):
    if send_as_id is None:
        send_as_id = get_current_identity_id()
    send_as_id = int(send_as_id)
    return f"{get_send_as_label(send_as_id)}[{send_as_id}]"


def resolve_identity_selector(selector):
    identity_id, _error = resolve_identity_selector_detail(selector)
    return identity_id


def _identity_selector_key(value):
    return str(value or "").strip().lstrip("@").casefold()


def _identity_selector_candidates(identity_id):
    profile = get_send_as_profile(identity_id)
    raw_candidates = [
        str(identity_id),
        profile.get("username") or "",
        profile.get("label") or "",
        get_send_as_label(identity_id),
        profile.get("daohao") or "",
        get_identity_ui_display_name(identity_id),
    ]
    candidates = []
    seen = set()
    for raw in raw_candidates:
        text = str(raw or "").strip()
        key = _identity_selector_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        candidates.append((text, key))
    return candidates


def _format_identity_selector_matches(matches):
    labels = []
    for identity_id, matched_text in matches:
        try:
            label = get_identity_ui_display_name(identity_id)
        except Exception:
            label = get_identity_display_name(identity_id)
        matched = str(matched_text or "").strip()
        labels.append(f"{label}({matched})" if matched else label)
    return "、".join(labels)


def resolve_identity_selector_detail(selector, *, allow_prefix=True, min_prefix_len=2):
    selector = (selector or "").strip()
    if not selector:
        return None, "未指定身份"

    normalized = selector.lstrip("@")
    if normalized.isdigit():
        identity_id = int(normalized)
        if identity_id in get_identity_ids():
            return identity_id, ""

    selector_key = _identity_selector_key(normalized)
    exact_matches = []
    for identity_id in get_identity_ids():
        for candidate, candidate_key in _identity_selector_candidates(identity_id):
            if selector_key == candidate_key:
                exact_matches.append((identity_id, candidate))
                break
    exact_ids = {identity_id for identity_id, _candidate in exact_matches}
    if len(exact_ids) == 1:
        return exact_matches[0][0], ""
    if len(exact_ids) > 1:
        return None, f"身份选择 {selector} 匹配多个身份：{_format_identity_selector_matches(exact_matches)}"

    if allow_prefix and selector_key and not selector_key.isdigit() and len(selector_key) >= int(min_prefix_len or 2):
        prefix_matches = []
        seen_ids = set()
        for identity_id in get_identity_ids():
            for candidate, candidate_key in _identity_selector_candidates(identity_id):
                if candidate_key.startswith(selector_key):
                    if identity_id not in seen_ids:
                        prefix_matches.append((identity_id, candidate))
                        seen_ids.add(identity_id)
                    break
        if len(prefix_matches) == 1:
            return prefix_matches[0][0], ""
        if len(prefix_matches) > 1:
            return None, f"身份选择 {selector} 匹配多个身份：{_format_identity_selector_matches(prefix_matches)}"

    return None, f"找不到身份：{selector}"


def split_command_identity_selector(text):
    parts = (text or "").strip().rsplit(maxsplit=1)
    if len(parts) == 2:
        target_id, _error = resolve_identity_selector_detail(parts[1])
        if target_id is not None:
            return parts[0].strip(), target_id
    return (text or "").strip(), None


@contextmanager
def use_identity(send_as_id):
    send_as_id = int(send_as_id)
    if not has_identity(send_as_id):
        raise KeyError(f"unknown identity: {send_as_id}")
    if send_as_id not in _meta_state["identity_states"]:
        _meta_state["identity_states"][send_as_id] = new_identity_state()
    token = _current_identity_id.set(send_as_id)
    active_token = _identity_context_active.set(True)
    try:
        yield _meta_state["identity_states"][send_as_id]
    finally:
        _identity_context_active.reset(active_token)
        _current_identity_id.reset(token)


class StateProxy:
    _MISSING = object()

    def _bucket(self):
        return get_identity_state()

    def __getitem__(self, key):
        if key in META_STATE_KEYS:
            return _meta_state[key]
        return self._bucket()[key]

    def __setitem__(self, key, value):
        if key in META_STATE_KEYS:
            _meta_state[key] = value
            return
        self._bucket()[key] = value

    def get(self, key, default=None):
        if key in META_STATE_KEYS:
            return _meta_state.get(key, default)
        return self._bucket().get(key, default)

    def setdefault(self, key, default=None):
        if key in META_STATE_KEYS:
            return _meta_state.setdefault(key, default)
        return self._bucket().setdefault(key, default)

    def pop(self, key, default=_MISSING):
        if key in META_STATE_KEYS:
            if default is StateProxy._MISSING:
                return _meta_state.pop(key)
            return _meta_state.pop(key, default)
        if default is StateProxy._MISSING:
            return self._bucket().pop(key)
        return self._bucket().pop(key, default)

    def items(self):
        merged = {**self._bucket(), **_meta_state}
        return merged.items()

    def keys(self):
        merged = {**self._bucket(), **_meta_state}
        return merged.keys()

    def values(self):
        merged = {**self._bucket(), **_meta_state}
        return merged.values()


state = StateProxy()


__all__ = [
    "GLOBAL_STATE_DEFAULTS",
    "IDENTITY_BOOL_FIELDS",
    "IDENTITY_JSON_COLUMNS",
    "IDENTITY_MODULE_COLUMNS",
    "IDENTITY_RUNTIME_COLUMNS",
    "IDENTITY_STATE_TEMPLATE",
    "IDENTITY_TIMER_COLUMNS",
    "META_STATE_KEYS",
    "REALM_SORT_ORDER",
    "StateProxy",
    "ensure_identity_registered",
    "has_identity",
    "remove_identity",
    "get_active_identity_id",
    "get_current_identity_id",
    "get_game_group_id",
    "get_game_bot_ids",
    "get_game_listener_account_ids",
    "get_game_topic_id",
    "get_forum_topics",
    "get_forum_topics_updated_at",
    "get_global_enabled",
    "get_tiandao_judgement_enabled",
    "get_guanxing_monitor_enabled",
    "get_guanxing_monitor_target_options",
    "get_guanxing_monitor_targets",
    "get_guanxing_shift_delay_sec",
    "get_guanxing_shift_target",
    "get_formation_run_state",
    "has_active_identity_context",
    "get_quiz_learning_watchers",
    "is_auto_delete_sent_messages_enabled",
    "get_identity_display_name",
    "get_identity_enabled",
    "get_identity_ui_display_name",
    "get_identity_ids",
    "get_identity_state",
    "convert_window_hours_local_to_utc",
    "format_window_text",
    "get_module_window_hours",
    "get_module_window_hours_local",
    "get_module_window_profile_keys",
    "get_pending_command",
    "get_available_module_names",
    "get_quiz_ai_config",
    "get_dungeon_join_run_state",
    "get_replica_group_id",
    "get_replica_group_ids",
    "get_replica_gold_dps_enabled",
    "get_replica_listener_account_id",
    "get_replica_listener_account_map",
    "get_replica_dispatch_group_ids",
    "get_replica_dispatch_listener_account_map",
    "get_replica_participant_identity_ids",
    "get_replica_dispatch_participant_identity_ids",
    "get_replica_query_aggregator_config",
    "get_replica_run_state",
    "get_replica_success_cooldown_hours",
    "get_replica_virtual_hall_match_enabled_map",
    "infer_replica_professions",
    "is_replica_gold_dps_allowed",
    "is_replica_query_aggregator_configured",
    "is_replica_virtual_hall_match_enabled",
    "is_storage_bag_api_configured",
    "get_jiyin_choice",
    "get_nanlong_choice",
    "get_pet_command",
    "get_pet_name",
    "get_pet_warm_name",
    "get_pet_warm_command",
    "get_pet_trial_name",
    "get_pet_trial_command",
    "get_pet_formation_command",
    "get_stargazer_star_choice",
    "get_divination_daily_limit",
    "get_divination_pending_exchanges",
    "get_divination_run_state",
    "get_world_boss_run_state",
    "get_storage_bag_api_config",
    "get_storage_bag_records",
    "get_storage_bag_item_rules",
    "get_tianjige_dao_path_records",
    "get_stargazer_total_slots",
    "get_tianti_rank_choice",
    "get_wild_training_strategy",
    "get_realm_sort_index",
    "get_realm_sort_key",
    "is_explore_rift_realm_available",
    "get_send_as_label",
    "is_module_available",
    "is_yuanying_realm_available",
    "is_small_world_realm_available",
    "get_send_as_profile",
    "get_send_as_tags",
    "new_identity_state",
    "resolve_identity_selector",
    "resolve_identity_selector_detail",
    "set_game_group_id",
    "set_game_bot_ids",
    "set_game_listener_account_ids",
    "set_game_topic_id",
    "set_dungeon_join_run_state",
    "set_replica_group_id",
    "set_replica_group_ids",
    "set_replica_gold_dps_enabled",
    "set_replica_listener_account_id",
    "set_replica_listener_account_map",
    "set_replica_dispatch_group_ids",
    "set_replica_dispatch_listener_account_map",
    "set_replica_participant_identity_ids",
    "set_replica_dispatch_participant_identity_ids",
    "set_replica_query_aggregator_config",
    "set_replica_run_state",
    "set_replica_success_cooldown_hours",
    "set_replica_virtual_hall_match_enabled",
    "set_replica_virtual_hall_match_enabled_map",
    "set_forum_topics",
    "set_global_enabled",
    "set_tiandao_judgement_enabled",
    "set_guanxing_monitor_enabled",
    "set_guanxing_monitor_targets",
    "set_guanxing_shift_delay_sec",
    "set_guanxing_shift_target",
    "set_formation_run_state",
    "set_quiz_ai_config",
    "set_quiz_learning_watchers",
    "set_auto_delete_sent_messages",
    "set_identity_enabled",
    "set_jiyin_choice",
    "set_nanlong_choice",
    "set_module_window_hours",
    "set_pet_name",
    "set_pet_warm_name",
    "set_pet_trial_name",
    "set_stargazer_star_choice",
    "set_divination_daily_limit",
    "set_divination_pending_exchanges",
    "set_divination_run_state",
    "set_world_boss_run_state",
    "set_storage_bag_api_config",
    "set_storage_bag_records",
    "set_storage_bag_item_rules",
    "set_tianjige_dao_path_records",
    "set_stargazer_total_slots",
    "set_tianti_rank_choice",
    "set_wild_training_strategy",
    "normalize_divination_daily_limit",
    "set_send_as_profile",
    "split_command_identity_selector",
    "update_send_as_profile",
    "state",
    "use_identity",
]
