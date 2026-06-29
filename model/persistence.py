import json
import os
import shutil
import sqlite3
import time
import traceback

from .config import DB_FILE, DB_SCHEMA_VERSION, DIVINATION_DEFAULT_DAILY_LIMIT, FLUSH_INTERVAL_SEC, RETRY_LIMIT
from .delayed_actions import (
    DELAYED_ACTIONS_STATE_KEY,
    export_to_state as export_delayed_actions_to_state,
    restore_from_state as restore_delayed_actions_from_state,
)
from .state import (
    IDENTITY_BOOL_FIELDS,
    IDENTITY_JSON_COLUMNS,
    IDENTITY_MODULE_COLUMNS,
    IDENTITY_RUNTIME_COLUMNS,
    IDENTITY_STATE_TEMPLATE,
    IDENTITY_TIMER_COLUMNS,
    ensure_identity_registered,
    get_game_group_id,
    get_game_bot_ids,
    get_game_topic_id,
    get_forum_topics,
    get_forum_topics_updated_at,
    is_auto_delete_sent_messages_enabled,
    get_global_enabled,
    get_tiandao_judgement_enabled,
    get_dungeon_join_run_state,
    get_formation_run_state,
    get_guanxing_monitor_enabled,
    get_guanxing_monitor_targets,
    get_guanxing_round_state,
    get_guanxing_shift_delay_sec,
    get_guanxing_shift_target,
    get_identity_ids,
    get_identity_state,
    get_pending_command,
    get_divination_pending_exchanges,
    get_divination_run_state,
    get_world_boss_run_state,
    get_quiz_ai_config,
    get_replica_group_id,
    get_replica_group_ids,
    get_replica_dispatch_group_ids,
    get_replica_dispatch_listener_account_map,
    get_replica_dispatch_participant_identity_ids,
    get_replica_listener_account_id,
    get_replica_listener_account_map,
    get_replica_participant_identity_ids,
    get_replica_query_aggregator_config,
    get_replica_run_state,
    get_replica_virtual_hall_match_enabled_map,
    get_send_as_profile,
    get_storage_bag_api_config,
    get_storage_bag_item_rules,
    get_storage_bag_records,
    get_tianjige_dao_path_records,
    new_identity_state,
    set_auto_delete_sent_messages,
    set_dungeon_join_run_state,
    set_formation_run_state,
    set_forum_topics,
    set_global_enabled,
    set_tiandao_judgement_enabled,
    set_game_bot_ids,
    get_quiz_learning_watchers,
    set_game_group_id,
    set_game_topic_id,
    set_divination_pending_exchanges,
    set_divination_run_state,
    set_world_boss_run_state,
    set_guanxing_monitor_enabled,
    set_guanxing_monitor_targets,
    set_guanxing_round_state,
    set_guanxing_shift_delay_sec,
    set_guanxing_shift_target,
    set_quiz_ai_config,
    set_quiz_learning_watchers,
    set_replica_group_id,
    set_replica_group_ids,
    set_replica_dispatch_group_ids,
    set_replica_dispatch_listener_account_map,
    set_replica_dispatch_participant_identity_ids,
    set_replica_listener_account_id,
    set_replica_listener_account_map,
    set_replica_participant_identity_ids,
    set_replica_query_aggregator_config,
    set_replica_run_state,
    set_replica_virtual_hall_match_enabled_map,
    set_send_as_profile,
    set_storage_bag_api_config,
    set_storage_bag_item_rules,
    set_storage_bag_records,
    set_tianjige_dao_path_records,
    get_accounts,
    set_accounts,
    get_identity_account_map,
    set_identity_account_map,
    _meta_state,
)
from .timing import configure_timing

_db_conn = None
_db_initialized = False
_schema_columns_ensured_key = None
_state_dirty = False
_last_flush_time = 0
_last_save_failed_at = 0.0
_last_save_error = ""
SMALL_WORLD_PREACH_DEFAULT_NORMALIZED_KEY = "small_world_preach_default_normalized"

LIVE_GUARD_DIR = os.path.abspath(os.environ.get("XIUXIAN_LIVE_GUARD_DIR") or "/root/xiuxian-main-live-guard")
LIVE_GUARD_DB_FILE = os.path.join(LIVE_GUARD_DIR, "chaogu_state.last-good.db")
LIVE_GUARD_MANIFEST_FILE = os.path.join(LIVE_GUARD_DIR, "manifest.json")


def _safety_watchdog_fused_file():
    return os.path.join(os.path.dirname(os.path.abspath(DB_FILE)), "safety_watchdog_fused.json")


def get_db_conn():
    global _db_conn, _schema_columns_ensured_key
    if _db_conn is None:
        _schema_columns_ensured_key = None
        _db_conn = sqlite3.connect(DB_FILE)
        _db_conn.row_factory = sqlite3.Row
    return _db_conn


def _schema_columns_cache_key(conn):
    return (os.path.abspath(DB_FILE), id(conn))


def _mark_schema_columns_ensured(conn):
    global _schema_columns_ensured_key
    _schema_columns_ensured_key = _schema_columns_cache_key(conn)


def _ensure_schema_columns_ready(conn):
    if _schema_columns_ensured_key == _schema_columns_cache_key(conn):
        return
    _ensure_schema_columns(conn)


def _mark_persistence_save_ok():
    global _last_save_failed_at, _last_save_error
    _last_save_failed_at = 0.0
    _last_save_error = ""


def _mark_persistence_save_failed(exc):
    global _last_save_failed_at, _last_save_error
    _last_save_failed_at = time.time()
    _last_save_error = str(exc or "").strip()[:240]


def has_persistence_write_failure():
    return _last_save_failed_at > 0


def get_persistence_write_failure():
    return {
        "failed_at": _last_save_failed_at,
        "error": _last_save_error,
    }


def _get_schema_version(conn):
    row = conn.execute("SELECT value FROM meta WHERE key = ?", ("schema_version",)).fetchone()
    if not row:
        return 0
    try:
        return int(row["value"] or 0)
    except (TypeError, ValueError, KeyError):
        return 0


def _ensure_schema_columns(conn):
    module_columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_module_state)").fetchall()}
    if "quiz_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN quiz_enabled INTEGER NOT NULL DEFAULT 1")
    if "jiyin_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN jiyin_enabled INTEGER NOT NULL DEFAULT 0")
    if "pet_trial_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN pet_trial_enabled INTEGER NOT NULL DEFAULT 0")
    if "pet_warm_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN pet_warm_enabled INTEGER NOT NULL DEFAULT 0")
    if "ranch_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN ranch_enabled INTEGER NOT NULL DEFAULT 0")
    if "wild_training_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN wild_training_enabled INTEGER NOT NULL DEFAULT 0")
    if "concubine_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN concubine_enabled INTEGER NOT NULL DEFAULT 0")
    if "concubine_tianji_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN concubine_tianji_enabled INTEGER NOT NULL DEFAULT 0")
    if "concubine_heart_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN concubine_heart_enabled INTEGER NOT NULL DEFAULT 0")
    if "concubine_voyage_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN concubine_voyage_enabled INTEGER NOT NULL DEFAULT 0")
    if "concubine_auto_reacquire" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN concubine_auto_reacquire INTEGER NOT NULL DEFAULT 1")
    if "hehuan_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN hehuan_enabled INTEGER NOT NULL DEFAULT 0")
    if "tianxing_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN tianxing_enabled INTEGER NOT NULL DEFAULT 0")
    if "yinluo_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN yinluo_enabled INTEGER NOT NULL DEFAULT 0")
    if "world_boss_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN world_boss_enabled INTEGER NOT NULL DEFAULT 0")
    if "nanlong_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN nanlong_enabled INTEGER NOT NULL DEFAULT 0")
    if "guanxing_monitor_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN guanxing_monitor_enabled INTEGER NOT NULL DEFAULT 0")
    if "guanxing_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN guanxing_enabled INTEGER NOT NULL DEFAULT 0")
    if "formation_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN formation_enabled INTEGER NOT NULL DEFAULT 0")
    if "last_guanxing_done_day" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN last_guanxing_done_day TEXT NOT NULL DEFAULT ''")
    if "tianti_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN tianti_enabled INTEGER NOT NULL DEFAULT 0")
    if "tianti_wenxin_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN tianti_wenxin_enabled INTEGER NOT NULL DEFAULT 1")
    if "tianti_gangfeng_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN tianti_gangfeng_enabled INTEGER NOT NULL DEFAULT 1")
    if "small_world_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN small_world_enabled INTEGER NOT NULL DEFAULT 0")
    if "small_world_preach_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN small_world_preach_enabled INTEGER NOT NULL DEFAULT 0")
    if "small_world_manifest_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN small_world_manifest_enabled INTEGER NOT NULL DEFAULT 0")
    if "small_world_harvest_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN small_world_harvest_enabled INTEGER NOT NULL DEFAULT 0")
    if "small_world_refine_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN small_world_refine_enabled INTEGER NOT NULL DEFAULT 0")
    if "small_world_refresh_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN small_world_refresh_enabled INTEGER NOT NULL DEFAULT 0")
    if "small_world_barrier_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN small_world_barrier_enabled INTEGER NOT NULL DEFAULT 1")
    if "small_world_barrier_min_stock" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN small_world_barrier_min_stock INTEGER NOT NULL DEFAULT 130000")
    if "small_world_barrier_guard_before_min" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN small_world_barrier_guard_before_min INTEGER NOT NULL DEFAULT 30")
    if "small_world_barrier_min_interval_hours" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN small_world_barrier_min_interval_hours REAL NOT NULL DEFAULT 18")
    if "divination_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN divination_enabled INTEGER NOT NULL DEFAULT 0")
    if "divination_daily_limit" not in module_columns:
        conn.execute(f"ALTER TABLE identity_module_state ADD COLUMN divination_daily_limit INTEGER NOT NULL DEFAULT {int(DIVINATION_DEFAULT_DAILY_LIMIT)}")
    if "dungeon_join_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN dungeon_join_enabled INTEGER NOT NULL DEFAULT 0")
    if "wendao_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN wendao_enabled INTEGER NOT NULL DEFAULT 0")
    if "duel_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN duel_enabled INTEGER NOT NULL DEFAULT 0")
    if "fishing_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN fishing_enabled INTEGER NOT NULL DEFAULT 0")
    if "explore_rift_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN explore_rift_enabled INTEGER NOT NULL DEFAULT 0")
    if "sect_teach_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN sect_teach_enabled INTEGER NOT NULL DEFAULT 0")

    identity_columns = {row[1] for row in conn.execute("PRAGMA table_info(identities)").fetchall()}
    if "pet_name" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN pet_name TEXT NOT NULL DEFAULT ''")
    if "pet_trial_name" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN pet_trial_name TEXT NOT NULL DEFAULT ''")
    if "pet_warm_name" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN pet_warm_name TEXT NOT NULL DEFAULT ''")
    if "daohao" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN daohao TEXT NOT NULL DEFAULT ''")
    if "realm" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN realm TEXT NOT NULL DEFAULT ''")
    if "spiritual_root_type" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN spiritual_root_type TEXT NOT NULL DEFAULT ''")
    if "spiritual_root_attrs" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN spiritual_root_attrs TEXT NOT NULL DEFAULT ''")
    if "replica_professions" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN replica_professions TEXT NOT NULL DEFAULT ''")
    if "replica_gold_dps_enabled" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN replica_gold_dps_enabled INTEGER NOT NULL DEFAULT 0")
    if "sect_name" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN sect_name TEXT NOT NULL DEFAULT ''")
    if "sect_updated_at" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN sect_updated_at REAL NOT NULL DEFAULT 0")
    if "jiyin_choice" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN jiyin_choice TEXT NOT NULL DEFAULT ''")
    if "nanlong_choice" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN nanlong_choice TEXT NOT NULL DEFAULT 'reject'")
    if "stargazer_star_choice" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN stargazer_star_choice TEXT NOT NULL DEFAULT '赤血星'")
    if "stargazer_total_slots" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN stargazer_total_slots INTEGER NOT NULL DEFAULT 0")
    if "tianti_rank_choice" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN tianti_rank_choice TEXT NOT NULL DEFAULT '普通'")
    if "checkin_window_start_hour_utc" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN checkin_window_start_hour_utc INTEGER NOT NULL DEFAULT 2")
    if "checkin_window_end_hour_utc" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN checkin_window_end_hour_utc INTEGER NOT NULL DEFAULT 3")
    if "tower_window_start_hour_utc" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN tower_window_start_hour_utc INTEGER NOT NULL DEFAULT 1")
    if "tower_window_end_hour_utc" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN tower_window_end_hour_utc INTEGER NOT NULL DEFAULT 2")
    if "enabled" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1")
    if "xiuwei_current" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN xiuwei_current INTEGER NOT NULL DEFAULT 0")
    if "xiuwei_max" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN xiuwei_max INTEGER NOT NULL DEFAULT 0")
    if "battle_power_text" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN battle_power_text TEXT NOT NULL DEFAULT ''")
    if "battle_power_value" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN battle_power_value INTEGER NOT NULL DEFAULT 0")

    timer_columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_timers)").fetchall()}
    if "next_quiz_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_quiz_time REAL NOT NULL DEFAULT 0")
    if "next_jiyin_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_jiyin_time REAL NOT NULL DEFAULT 0")
    if "next_pet_trial_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_pet_trial_time REAL NOT NULL DEFAULT 0")
    if "next_pet_warm_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_pet_warm_time REAL NOT NULL DEFAULT 0")
    if "next_ranch_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_ranch_time REAL NOT NULL DEFAULT 0")
    if "next_wild_training_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_wild_training_time REAL NOT NULL DEFAULT 0")
    if "next_concubine_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_concubine_time REAL NOT NULL DEFAULT 0")
    if "next_nanlong_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_nanlong_time REAL NOT NULL DEFAULT 0")
    if "next_stargazer_panel_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_stargazer_panel_time REAL NOT NULL DEFAULT 0")
    if "stargazer_collect_due_at" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN stargazer_collect_due_at REAL NOT NULL DEFAULT 0")
    if "next_guanxing_monitor_notify_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_guanxing_monitor_notify_time REAL NOT NULL DEFAULT 0")
    if "next_tianti_status_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_tianti_status_time REAL NOT NULL DEFAULT 0")
    if "next_tianti_wenxin_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_tianti_wenxin_time REAL NOT NULL DEFAULT 0")
    if "next_tianti_climb_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_tianti_climb_time REAL NOT NULL DEFAULT 0")
    if "next_tianti_gangfeng_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_tianti_gangfeng_time REAL NOT NULL DEFAULT 0")
    if "next_small_world_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_small_world_time REAL NOT NULL DEFAULT 0")
    if "next_explore_rift_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_explore_rift_time REAL NOT NULL DEFAULT 0")
    if "next_wendao_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_wendao_time REAL NOT NULL DEFAULT 0")
    if "next_duel_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_duel_time REAL NOT NULL DEFAULT 0")
    if "next_fishing_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_fishing_time REAL NOT NULL DEFAULT 0")
    if "next_formation_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_formation_time REAL NOT NULL DEFAULT 0")
    if "formation_cooldown_until" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN formation_cooldown_until REAL NOT NULL DEFAULT 0")
    if "weak_until" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN weak_until REAL NOT NULL DEFAULT 0")

    runtime_columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_runtime_state)").fetchall()}
    if "weak_reason" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN weak_reason TEXT NOT NULL DEFAULT ''")
    if "weak_source" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN weak_source TEXT NOT NULL DEFAULT ''")
    if "weak_last_block_log_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN weak_last_block_log_at REAL NOT NULL DEFAULT 0")
    if "yuanying_waiting_logged" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN yuanying_waiting_logged INTEGER NOT NULL DEFAULT 0")
    if "yuanying_protect_logged" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN yuanying_protect_logged INTEGER NOT NULL DEFAULT 0")
    if "deep_retreat_waiting_logged" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN deep_retreat_waiting_logged INTEGER NOT NULL DEFAULT 0")
    if "deep_retreat_protect_logged" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN deep_retreat_protect_logged INTEGER NOT NULL DEFAULT 0")
    if "tree_maturing_logged" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_maturing_logged INTEGER NOT NULL DEFAULT 0")
    if "tree_harvest_followup_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_harvest_followup_due_at REAL NOT NULL DEFAULT 0")
    if "tree_harvest_inflight_until" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_harvest_inflight_until REAL NOT NULL DEFAULT 0")
    if "tree_last_harvest_result_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_last_harvest_result_msg_id INTEGER NOT NULL DEFAULT 0")
    if "tree_last_harvest_reply_to_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_last_harvest_reply_to_msg_id INTEGER NOT NULL DEFAULT 0")
    if "tree_bootstrap_check_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_bootstrap_check_due_at REAL NOT NULL DEFAULT 0")
    if "last_tree_status_sent_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN last_tree_status_sent_at REAL NOT NULL DEFAULT 0")
    if "tree_pulse_mode_seen" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_mode_seen INTEGER NOT NULL DEFAULT 0")
    if "tree_pulse_last_panel_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_last_panel_at REAL NOT NULL DEFAULT 0")
    if "tree_pulse_progress" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_progress REAL NOT NULL DEFAULT 0")
    if "tree_pulse_main" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_main TEXT NOT NULL DEFAULT ''")
    if "tree_pulse_aux" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_aux TEXT NOT NULL DEFAULT ''")
    if "tree_pulse_reverse" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_reverse TEXT NOT NULL DEFAULT ''")
    if "tree_pulse_neutral" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_neutral TEXT NOT NULL DEFAULT ''")
    if "tree_pulse_stability" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_stability INTEGER NOT NULL DEFAULT 0")
    if "tree_pulse_stability_max" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_stability_max INTEGER NOT NULL DEFAULT 0")
    if "tree_pulse_turbidity" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_turbidity INTEGER NOT NULL DEFAULT 0")
    if "tree_pulse_turbidity_max" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_turbidity_max INTEGER NOT NULL DEFAULT 0")
    if "tree_pulse_daily_used" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_daily_used INTEGER NOT NULL DEFAULT 0")
    if "tree_pulse_daily_limit" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_daily_limit INTEGER NOT NULL DEFAULT 0")
    if "tree_pulse_rush_used" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_rush_used INTEGER NOT NULL DEFAULT 0")
    if "tree_pulse_rush_limit" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_rush_limit INTEGER NOT NULL DEFAULT 0")
    if "tree_pulse_last_action" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_last_action TEXT NOT NULL DEFAULT ''")
    if "tree_pulse_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_last_error TEXT NOT NULL DEFAULT ''")
    if "tree_pulse_blocked_until" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tree_pulse_blocked_until REAL NOT NULL DEFAULT 0")
    if "last_tower_command_sent_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN last_tower_command_sent_at REAL NOT NULL DEFAULT 0")
    if "tower_reply_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tower_reply_due_at REAL NOT NULL DEFAULT 0")
    if "tower_retry_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tower_retry_count INTEGER NOT NULL DEFAULT 0")
    if "concubine_phase" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_phase TEXT NOT NULL DEFAULT 'idle'")
    if "concubine_availability" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_availability TEXT NOT NULL DEFAULT 'unknown'")
    if "concubine_nanlong_strategy" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_nanlong_strategy TEXT NOT NULL DEFAULT 'reacquire_after_loss'")
    if "concubine_status_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_status_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_greet_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_greet_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_last_greet_day" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_last_greet_day TEXT NOT NULL DEFAULT ''")
    if "concubine_greet_retry_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_greet_retry_count INTEGER NOT NULL DEFAULT 0")
    if "concubine_greet_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_greet_last_error TEXT NOT NULL DEFAULT ''")
    if "concubine_gift_status_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_gift_status_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_gift_bag_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_gift_bag_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_gift_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_gift_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_gift_amount" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_gift_amount INTEGER NOT NULL DEFAULT 0")
    if "concubine_last_gift_day" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_last_gift_day TEXT NOT NULL DEFAULT ''")
    if "concubine_gift_attempt_day" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_gift_attempt_day TEXT NOT NULL DEFAULT ''")
    if "concubine_gift_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_gift_last_error TEXT NOT NULL DEFAULT ''")
    if "concubine_dream_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_dream_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_fragment_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_fragment_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_puzzle_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_puzzle_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_reacquire_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_reacquire_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_tianji_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_tianji_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_heart_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_heart_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_heart_prompt_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_heart_prompt_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_voyage_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_voyage_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_voyage_retry_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_voyage_retry_count INTEGER NOT NULL DEFAULT 0")
    if "concubine_last_panel_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_last_panel_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_name" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_name TEXT NOT NULL DEFAULT ''")
    if "concubine_kind" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_kind TEXT NOT NULL DEFAULT ''")
    if "concubine_location" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_location TEXT NOT NULL DEFAULT ''")
    if "concubine_affinity" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_affinity INTEGER NOT NULL DEFAULT 0")
    if "concubine_oath" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_oath TEXT NOT NULL DEFAULT ''")
    if "concubine_dream_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_dream_due_at REAL NOT NULL DEFAULT 0")
    if "concubine_tianji_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_tianji_due_at REAL NOT NULL DEFAULT 0")
    if "concubine_heart_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_heart_due_at REAL NOT NULL DEFAULT 0")
    if "concubine_tianji_chain" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_tianji_chain TEXT NOT NULL DEFAULT ''")
    if "concubine_tianji_chain_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_tianji_chain_due_at REAL NOT NULL DEFAULT 0")
    if "concubine_heart_round" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_heart_round INTEGER NOT NULL DEFAULT 0")
    if "concubine_heart_choice_prompt_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_heart_choice_prompt_msg_id INTEGER NOT NULL DEFAULT 0")
    if "concubine_heart_choice_round" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_heart_choice_round INTEGER NOT NULL DEFAULT 0")
    if "concubine_heart_choice_sent_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_heart_choice_sent_at REAL NOT NULL DEFAULT 0")
    if "concubine_heart_choice_retry_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_heart_choice_retry_count INTEGER NOT NULL DEFAULT 0")
    if "concubine_last_recovered_reply_key" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_last_recovered_reply_key TEXT NOT NULL DEFAULT ''")
    if "concubine_last_recovered_reply_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_last_recovered_reply_at REAL NOT NULL DEFAULT 0")
    if "concubine_fragment_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_fragment_count INTEGER NOT NULL DEFAULT 0")
    if "concubine_fragment_total" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_fragment_total INTEGER NOT NULL DEFAULT 4")
    if "concubine_fragment_xutian_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_fragment_xutian_count INTEGER NOT NULL DEFAULT 0")
    if "concubine_fragment_xutian_total" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_fragment_xutian_total INTEGER NOT NULL DEFAULT 4")
    if "concubine_fragment_cangkun_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_fragment_cangkun_count INTEGER NOT NULL DEFAULT 0")
    if "concubine_fragment_cangkun_total" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_fragment_cangkun_total INTEGER NOT NULL DEFAULT 4")
    if "concubine_fragment_confirm_key" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_fragment_confirm_key TEXT NOT NULL DEFAULT ''")
    if "concubine_fragment_confirmed_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_fragment_confirmed_at REAL NOT NULL DEFAULT 0")
    if "concubine_voyage_status" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_voyage_status TEXT NOT NULL DEFAULT ''")
    if "concubine_voyage_route" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_voyage_route TEXT NOT NULL DEFAULT ''")
    if "concubine_voyage_return_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_voyage_return_at REAL NOT NULL DEFAULT 0")
    if "concubine_voyage_last_result" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_voyage_last_result TEXT NOT NULL DEFAULT ''")
    if "concubine_voyage_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_voyage_last_error TEXT NOT NULL DEFAULT ''")
    conn.execute("""
        UPDATE identity_runtime_state
           SET concubine_fragment_xutian_count = concubine_fragment_count,
               concubine_fragment_xutian_total = concubine_fragment_total
         WHERE COALESCE(concubine_fragment_xutian_count, 0) = 0
           AND COALESCE(concubine_fragment_count, 0) != 0
    """)
    if "concubine_last_snapshot_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_last_snapshot_at REAL NOT NULL DEFAULT 0")
    if "concubine_reacquire_blocked_until" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_reacquire_blocked_until REAL NOT NULL DEFAULT 0")
    if "concubine_reacquire_attempts" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_reacquire_attempts INTEGER NOT NULL DEFAULT 0")
    if "concubine_reacquire_command_override" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_reacquire_command_override TEXT NOT NULL DEFAULT ''")
    if "concubine_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_last_error TEXT NOT NULL DEFAULT ''")
    if "concubine_tianji_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_tianji_last_error TEXT NOT NULL DEFAULT ''")
    if "concubine_heart_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN concubine_heart_last_error TEXT NOT NULL DEFAULT ''")
    if "hehuan_observation" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN hehuan_observation TEXT NOT NULL DEFAULT '{}' ")
    if "tianxing_observation" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianxing_observation TEXT NOT NULL DEFAULT '{}' ")
    if "tianxing_auto_config" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianxing_auto_config TEXT NOT NULL DEFAULT '{}' ")
    if "tianxing_timeline_state" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianxing_timeline_state TEXT NOT NULL DEFAULT '{}' ")
    if "yinluo_observation" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN yinluo_observation TEXT NOT NULL DEFAULT '{}' ")
    if "world_boss_action_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN world_boss_action_count INTEGER NOT NULL DEFAULT 0")
    if "world_boss_action_limit" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN world_boss_action_limit INTEGER NOT NULL DEFAULT 5")
    if "world_boss_attack_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN world_boss_attack_count INTEGER NOT NULL DEFAULT 0")
    if "world_boss_pending_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN world_boss_pending_msg_id INTEGER NOT NULL DEFAULT 0")
    if "world_boss_pending_action" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN world_boss_pending_action TEXT NOT NULL DEFAULT ''")
    if "world_boss_pending_since" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN world_boss_pending_since REAL NOT NULL DEFAULT 0")
    if "world_boss_pending_retry_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN world_boss_pending_retry_count INTEGER NOT NULL DEFAULT 0")
    if "world_boss_pending_action_seq" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN world_boss_pending_action_seq INTEGER NOT NULL DEFAULT 0")
    if "world_boss_last_action" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN world_boss_last_action TEXT NOT NULL DEFAULT ''")
    if "world_boss_last_action_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN world_boss_last_action_at REAL NOT NULL DEFAULT 0")
    if "world_boss_last_reply_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN world_boss_last_reply_msg_id INTEGER NOT NULL DEFAULT 0")
    if "world_boss_exhausted" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN world_boss_exhausted INTEGER NOT NULL DEFAULT 0")
    if "world_boss_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN world_boss_last_error TEXT NOT NULL DEFAULT ''")
    if "pet_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN pet_last_error TEXT NOT NULL DEFAULT ''")
    if "pet_trial_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN pet_trial_last_error TEXT NOT NULL DEFAULT ''")
    if "pet_warm_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN pet_warm_last_error TEXT NOT NULL DEFAULT ''")
    if "ranch_reply_to_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN ranch_reply_to_msg_id INTEGER NOT NULL DEFAULT 0")
    if "ranch_reply_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN ranch_reply_due_at REAL NOT NULL DEFAULT 0")
    if "ranch_retry_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN ranch_retry_count INTEGER NOT NULL DEFAULT 0")
    if "ranch_last_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN ranch_last_msg_id INTEGER NOT NULL DEFAULT 0")
    if "ranch_last_result" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN ranch_last_result TEXT NOT NULL DEFAULT ''")
    if "ranch_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN ranch_last_error TEXT NOT NULL DEFAULT ''")
    if "ranch_return_pending" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN ranch_return_pending INTEGER NOT NULL DEFAULT 0")
    if "ranch_return_seen_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN ranch_return_seen_msg_id INTEGER NOT NULL DEFAULT 0")
    if "ranch_return_wait_since" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN ranch_return_wait_since REAL NOT NULL DEFAULT 0")
    if "ranch_return_last_notified_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN ranch_return_last_notified_at REAL NOT NULL DEFAULT 0")
    if "wild_training_strategy" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wild_training_strategy TEXT NOT NULL DEFAULT '深入'")
    if "wild_training_reply_to_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wild_training_reply_to_msg_id INTEGER NOT NULL DEFAULT 0")
    if "wild_training_reply_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wild_training_reply_due_at REAL NOT NULL DEFAULT 0")
    if "wild_training_retry_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wild_training_retry_count INTEGER NOT NULL DEFAULT 0")
    if "wild_training_last_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wild_training_last_msg_id INTEGER NOT NULL DEFAULT 0")
    if "wild_training_last_result" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wild_training_last_result TEXT NOT NULL DEFAULT ''")
    if "wild_training_last_result_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wild_training_last_result_at REAL NOT NULL DEFAULT 0")
    if "wild_training_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wild_training_last_error TEXT NOT NULL DEFAULT ''")
    if "stargazer_last_panel_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN stargazer_last_panel_msg_id INTEGER NOT NULL DEFAULT 0")
    if "stargazer_last_action" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN stargazer_last_action TEXT NOT NULL DEFAULT ''")
    if "stargazer_queued_action" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN stargazer_queued_action TEXT NOT NULL DEFAULT ''")
    if "stargazer_idle_slot_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN stargazer_idle_slot_count INTEGER NOT NULL DEFAULT 0")
    if "stargazer_dim_slot_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN stargazer_dim_slot_count INTEGER NOT NULL DEFAULT 0")
    if "stargazer_ready_slot_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN stargazer_ready_slot_count INTEGER NOT NULL DEFAULT 0")
    if "stargazer_busy_until" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN stargazer_busy_until REAL NOT NULL DEFAULT 0")
    if "stargazer_followup_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN stargazer_followup_due_at REAL NOT NULL DEFAULT 0")
    if "stargazer_wait_full_collect" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN stargazer_wait_full_collect INTEGER NOT NULL DEFAULT 0")
    if "stargazer_collect_ready" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN stargazer_collect_ready INTEGER NOT NULL DEFAULT 0")
    if "stargazer_soothe_before_collect" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN stargazer_soothe_before_collect INTEGER NOT NULL DEFAULT 0")
    if "guanxing_monitor_slot_key" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_monitor_slot_key TEXT NOT NULL DEFAULT ''")
    if "guanxing_monitor_slot_start_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_monitor_slot_start_at REAL NOT NULL DEFAULT 0")
    if "guanxing_monitor_slot_end_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_monitor_slot_end_at REAL NOT NULL DEFAULT 0")
    if "guanxing_monitor_seen_panel" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_monitor_seen_panel INTEGER NOT NULL DEFAULT 0")
    if "guanxing_monitor_matched_keyword" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_monitor_matched_keyword TEXT NOT NULL DEFAULT ''")
    if "guanxing_monitor_matched_value" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_monitor_matched_value TEXT NOT NULL DEFAULT ''")
    if "guanxing_monitor_last_evolution_value" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_monitor_last_evolution_value TEXT NOT NULL DEFAULT ''")
    if "guanxing_monitor_last_seen_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_monitor_last_seen_at REAL NOT NULL DEFAULT 0")
    if "guanxing_monitor_last_notified_slot_key" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_monitor_last_notified_slot_key TEXT NOT NULL DEFAULT ''")
    if "guanxing_last_query_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_last_query_msg_id INTEGER NOT NULL DEFAULT 0")
    if "guanxing_last_panel_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_last_panel_msg_id INTEGER NOT NULL DEFAULT 0")
    if "guanxing_panel_slot_key" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_panel_slot_key TEXT NOT NULL DEFAULT ''")
    if "guanxing_last_panel_seen_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_last_panel_seen_at REAL NOT NULL DEFAULT 0")
    if "guanxing_last_shift_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_last_shift_msg_id INTEGER NOT NULL DEFAULT 0")
    if "guanxing_last_shift_slot_key" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_last_shift_slot_key TEXT NOT NULL DEFAULT ''")
    if "guanxing_last_shift_target" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_last_shift_target TEXT NOT NULL DEFAULT ''")
    if "guanxing_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN guanxing_last_error TEXT NOT NULL DEFAULT ''")
    if "last_formation_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN last_formation_msg_id INTEGER NOT NULL DEFAULT 0")
    if "formation_pending_invite_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN formation_pending_invite_msg_id INTEGER NOT NULL DEFAULT 0")
    if "formation_pending_assist_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN formation_pending_assist_msg_id INTEGER NOT NULL DEFAULT 0")
    if "formation_last_action" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN formation_last_action TEXT NOT NULL DEFAULT ''")
    if "formation_last_result" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN formation_last_result TEXT NOT NULL DEFAULT ''")
    if "formation_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN formation_last_error TEXT NOT NULL DEFAULT ''")
    if "formation_last_success_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN formation_last_success_at REAL NOT NULL DEFAULT 0")
    if "tianti_status_reply_to_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_status_reply_to_msg_id INTEGER NOT NULL DEFAULT 0")
    if "tianti_last_status_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_last_status_msg_id INTEGER NOT NULL DEFAULT 0")
    if "tianti_last_status_seen_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_last_status_seen_at REAL NOT NULL DEFAULT 0")
    if "tianti_last_wenxin_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_last_wenxin_msg_id INTEGER NOT NULL DEFAULT 0")
    if "tianti_last_climb_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_last_climb_msg_id INTEGER NOT NULL DEFAULT 0")
    if "tianti_last_gangfeng_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_last_gangfeng_msg_id INTEGER NOT NULL DEFAULT 0")
    if "tianti_progress_current" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_progress_current INTEGER NOT NULL DEFAULT 0")
    if "tianti_progress_total" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_progress_total INTEGER NOT NULL DEFAULT 12")
    if "tianti_cycle_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_cycle_count INTEGER NOT NULL DEFAULT 0")
    if "tianti_gangfeng_level" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_gangfeng_level INTEGER NOT NULL DEFAULT 0")
    if "tianti_gangfeng_total" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_gangfeng_total INTEGER NOT NULL DEFAULT 12")
    if "tianti_cooldown_text" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_cooldown_text TEXT NOT NULL DEFAULT '未记录'")
    if "tianti_wenxin_status" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_wenxin_status TEXT NOT NULL DEFAULT '未记录'")
    if "tianti_gangfeng_status" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_gangfeng_status TEXT NOT NULL DEFAULT '未记录'")
    if "tianti_remaining_climb_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_remaining_climb_count INTEGER NOT NULL DEFAULT 0")
    if "tianti_last_wenxin_day" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_last_wenxin_day TEXT NOT NULL DEFAULT ''")
    if "tianti_wenxin_last_trigger_key" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_wenxin_last_trigger_key TEXT NOT NULL DEFAULT ''")
    if "tianti_gangfeng_last_trigger_key" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_gangfeng_last_trigger_key TEXT NOT NULL DEFAULT ''")
    if "tianti_last_skip_reason" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_last_skip_reason TEXT NOT NULL DEFAULT ''")
    if "tianti_theoretical_max_stage" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_theoretical_max_stage INTEGER NOT NULL DEFAULT 0")
    if "tianti_wenxin_trigger_stage" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_wenxin_trigger_stage INTEGER NOT NULL DEFAULT 0")
    if "tianti_last_cost_xiuwei" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_last_cost_xiuwei INTEGER NOT NULL DEFAULT 0")
    if "tianti_last_gain_xiuwei" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_last_gain_xiuwei INTEGER NOT NULL DEFAULT 0")
    if "tianti_last_gain_contrib" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_last_gain_contrib INTEGER NOT NULL DEFAULT 0")
    if "tianti_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_last_error TEXT NOT NULL DEFAULT ''")
    if "stargazer_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN stargazer_enabled INTEGER NOT NULL DEFAULT 0")
    if "quiz_reply_to_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_reply_to_msg_id INTEGER NOT NULL DEFAULT 0")
    if "quiz_chat_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_chat_id INTEGER NOT NULL DEFAULT 0")
    if "quiz_question" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_question TEXT NOT NULL DEFAULT ''")
    if "quiz_options" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_options TEXT NOT NULL DEFAULT '{}' ")
    if "quiz_answer" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_answer TEXT NOT NULL DEFAULT ''")
    if "quiz_phase" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_phase TEXT NOT NULL DEFAULT ''")
    if "quiz_retry_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_retry_count INTEGER NOT NULL DEFAULT 0")
    if "quiz_match_mode" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_match_mode TEXT NOT NULL DEFAULT ''")
    if "quiz_answer_method" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_answer_method TEXT NOT NULL DEFAULT ''")
    if "quiz_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_last_error TEXT NOT NULL DEFAULT ''")
    if "quiz_last_matched_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_last_matched_at REAL NOT NULL DEFAULT 0")
    if "jiyin_reply_to_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN jiyin_reply_to_msg_id INTEGER NOT NULL DEFAULT 0")
    if "jiyin_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN jiyin_last_error TEXT NOT NULL DEFAULT ''")
    if "nanlong_reply_to_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN nanlong_reply_to_msg_id INTEGER NOT NULL DEFAULT 0")
    if "nanlong_reply_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN nanlong_reply_due_at REAL NOT NULL DEFAULT 0")
    if "nanlong_last_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN nanlong_last_msg_id INTEGER NOT NULL DEFAULT 0")
    if "nanlong_retry_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN nanlong_retry_count INTEGER NOT NULL DEFAULT 0")
    if "nanlong_last_command" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN nanlong_last_command TEXT NOT NULL DEFAULT ''")
    if "nanlong_protect_phase" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN nanlong_protect_phase TEXT NOT NULL DEFAULT ''")
    if "nanlong_place_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN nanlong_place_msg_id INTEGER NOT NULL DEFAULT 0")
    if "nanlong_recall_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN nanlong_recall_msg_id INTEGER NOT NULL DEFAULT 0")
    if "nanlong_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN nanlong_last_error TEXT NOT NULL DEFAULT ''")
    if "small_world_preach_reply_to_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_preach_reply_to_msg_id INTEGER NOT NULL DEFAULT 0")
    if "small_world_preach_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_preach_due_at REAL NOT NULL DEFAULT 0")
    if "small_world_god_cooldown_until" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_god_cooldown_until REAL NOT NULL DEFAULT 0")
    if "small_world_pending_god_action" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_pending_god_action TEXT NOT NULL DEFAULT ''")
    if "small_world_pending_god_reason" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_pending_god_reason TEXT NOT NULL DEFAULT ''")
    if "small_world_pending_god_priority" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_pending_god_priority INTEGER NOT NULL DEFAULT 0")
    if "small_world_pending_god_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_pending_god_at REAL NOT NULL DEFAULT 0")
    if "small_world_last_god_action" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_last_god_action TEXT NOT NULL DEFAULT ''")
    if "small_world_last_god_sent_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_last_god_sent_at REAL NOT NULL DEFAULT 0")
    if "small_world_last_disaster_wave_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_last_disaster_wave_at REAL NOT NULL DEFAULT 0")
    if "small_world_barrier_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_barrier_msg_id INTEGER NOT NULL DEFAULT 0")
    if "small_world_barrier_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_barrier_due_at REAL NOT NULL DEFAULT 0")
    if "small_world_last_barrier_sent_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_last_barrier_sent_at REAL NOT NULL DEFAULT 0")
    if "small_world_phase" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_phase TEXT NOT NULL DEFAULT 'idle'")
    if "small_world_query_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_query_msg_id INTEGER NOT NULL DEFAULT 0")
    if "small_world_manifest_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_manifest_msg_id INTEGER NOT NULL DEFAULT 0")
    if "small_world_manifest_cost_text" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_manifest_cost_text TEXT NOT NULL DEFAULT ''")
    if "small_world_harvest_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_harvest_msg_id INTEGER NOT NULL DEFAULT 0")
    if "small_world_refine_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_refine_msg_id INTEGER NOT NULL DEFAULT 0")
    if "small_world_refresh_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_refresh_count INTEGER NOT NULL DEFAULT 0")
    if "small_world_pending_incense" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_pending_incense REAL NOT NULL DEFAULT 0")
    if "small_world_incense_stock" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_incense_stock INTEGER NOT NULL DEFAULT 0")
    if "small_world_faith_value" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_faith_value INTEGER NOT NULL DEFAULT 0")
    if "small_world_panel_snapshot" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_panel_snapshot TEXT NOT NULL DEFAULT '{}' ")
    if "small_world_last_panel_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_last_panel_at REAL NOT NULL DEFAULT 0")
    if "small_world_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN small_world_last_error TEXT NOT NULL DEFAULT ''")
    if "resource_shortage_backoffs" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN resource_shortage_backoffs TEXT NOT NULL DEFAULT '{}' ")
    if "action_guard_sessions" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN action_guard_sessions TEXT NOT NULL DEFAULT '{}' ")
    if "wendao_reply_to_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wendao_reply_to_msg_id INTEGER NOT NULL DEFAULT 0")
    if "wendao_reply_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wendao_reply_due_at REAL NOT NULL DEFAULT 0")
    if "wendao_pending_result_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wendao_pending_result_msg_id INTEGER NOT NULL DEFAULT 0")
    if "wendao_sent_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wendao_sent_at REAL NOT NULL DEFAULT 0")
    if "wendao_last_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wendao_last_msg_id INTEGER NOT NULL DEFAULT 0")
    if "wendao_last_result" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wendao_last_result TEXT NOT NULL DEFAULT ''")
    if "wendao_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN wendao_last_error TEXT NOT NULL DEFAULT ''")
    if "duel_target" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN duel_target TEXT NOT NULL DEFAULT ''")
    if "duel_total_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN duel_total_count INTEGER NOT NULL DEFAULT 0")
    if "duel_completed_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN duel_completed_count INTEGER NOT NULL DEFAULT 0")
    if "duel_reply_to_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN duel_reply_to_msg_id INTEGER NOT NULL DEFAULT 0")
    if "duel_reply_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN duel_reply_due_at REAL NOT NULL DEFAULT 0")
    if "duel_open_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN duel_open_msg_id INTEGER NOT NULL DEFAULT 0")
    if "duel_magic_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN duel_magic_due_at REAL NOT NULL DEFAULT 0")
    if "duel_magic_sent_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN duel_magic_sent_at REAL NOT NULL DEFAULT 0")
    if "duel_started_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN duel_started_at REAL NOT NULL DEFAULT 0")
    if "duel_last_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN duel_last_msg_id INTEGER NOT NULL DEFAULT 0")
    if "duel_last_result" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN duel_last_result TEXT NOT NULL DEFAULT ''")
    if "duel_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN duel_last_error TEXT NOT NULL DEFAULT ''")
    if "fishing_pond" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_pond TEXT NOT NULL DEFAULT '青溪浅滩'")
    if "fishing_bait" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_bait TEXT NOT NULL DEFAULT '凡饵'")
    if "fishing_daily_limit" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_daily_limit INTEGER NOT NULL DEFAULT 20")
    if "fishing_daily_day" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_daily_day TEXT NOT NULL DEFAULT ''")
    if "fishing_daily_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_daily_count INTEGER NOT NULL DEFAULT 0")
    if "fishing_basket_calibrated_day" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_basket_calibrated_day TEXT NOT NULL DEFAULT ''")
    if "fishing_auto_chum_enabled" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_auto_chum_enabled INTEGER NOT NULL DEFAULT 1")
    if "fishing_chum_name" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_chum_name TEXT NOT NULL DEFAULT ''")
    if "fishing_chum_names" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_chum_names TEXT NOT NULL DEFAULT ''")
    if "fishing_chum_day" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_chum_day TEXT NOT NULL DEFAULT ''")
    if "fishing_chum_counts" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_chum_counts TEXT NOT NULL DEFAULT ''")
    if "fishing_auto_buy_bait_enabled" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_auto_buy_bait_enabled INTEGER NOT NULL DEFAULT 1")
    if "fishing_auto_buy_bait_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_auto_buy_bait_count INTEGER NOT NULL DEFAULT 20")
    if "fishing_auto_probe_enabled" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_auto_probe_enabled INTEGER NOT NULL DEFAULT 0")
    if "fishing_auto_open_fish_enabled" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_auto_open_fish_enabled INTEGER NOT NULL DEFAULT 1")
    if "fishing_cancel_after_sec" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_cancel_after_sec INTEGER NOT NULL DEFAULT 120")
    if "fishing_transfer_target_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_transfer_target_id INTEGER NOT NULL DEFAULT 0")
    if "fishing_transfer_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_transfer_due_at REAL NOT NULL DEFAULT 0")
    if "fishing_caught_fish_json" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_caught_fish_json TEXT NOT NULL DEFAULT ''")
    if "fishing_valuable_drop_reminders" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_valuable_drop_reminders TEXT NOT NULL DEFAULT '[]'")
    if "fishing_phase" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_phase TEXT NOT NULL DEFAULT 'idle'")
    if "fishing_reply_to_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_reply_to_msg_id INTEGER NOT NULL DEFAULT 0")
    if "fishing_reply_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_reply_due_at REAL NOT NULL DEFAULT 0")
    if "fishing_status_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_status_msg_id INTEGER NOT NULL DEFAULT 0")
    if "fishing_pending_action" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_pending_action TEXT NOT NULL DEFAULT ''")
    if "fishing_pending_open_fish" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_pending_open_fish TEXT NOT NULL DEFAULT ''")
    if "fishing_forced_buy_bait" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_forced_buy_bait TEXT NOT NULL DEFAULT ''")
    if "fishing_forced_buy_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_forced_buy_count INTEGER NOT NULL DEFAULT 0")
    if "fishing_started_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_started_at REAL NOT NULL DEFAULT 0")
    if "fishing_active_chum_name" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_active_chum_name TEXT NOT NULL DEFAULT ''")
    if "fishing_chum_rods_remaining" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_chum_rods_remaining INTEGER NOT NULL DEFAULT 0")
    if "fishing_last_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_last_msg_id INTEGER NOT NULL DEFAULT 0")
    if "fishing_last_result" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_last_result TEXT NOT NULL DEFAULT ''")
    if "fishing_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN fishing_last_error TEXT NOT NULL DEFAULT ''")
    if "explore_rift_reply_to_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_reply_to_msg_id INTEGER NOT NULL DEFAULT 0")
    if "explore_rift_reply_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_reply_due_at REAL NOT NULL DEFAULT 0")
    if "explore_rift_pending_result_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_pending_result_msg_id INTEGER NOT NULL DEFAULT 0")
    if "explore_rift_last_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_last_msg_id INTEGER NOT NULL DEFAULT 0")
    if "explore_rift_last_result" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_last_result TEXT NOT NULL DEFAULT ''")
    if "explore_rift_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_last_error TEXT NOT NULL DEFAULT ''")
    if "explore_rift_last_result_key" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_last_result_key TEXT NOT NULL DEFAULT ''")
    if "explore_rift_manual_required" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_manual_required INTEGER NOT NULL DEFAULT 0")
    if "explore_rift_nascent_escape_weak_until" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_nascent_escape_weak_until REAL NOT NULL DEFAULT 0")
    if "explore_rift_rebirth_required" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_required INTEGER NOT NULL DEFAULT 0")
    if "explore_rift_rebirth_phase" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_phase TEXT NOT NULL DEFAULT 'idle'")
    if "explore_rift_rebirth_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_due_at REAL NOT NULL DEFAULT 0")
    if "explore_rift_rebirth_request_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_request_msg_id INTEGER NOT NULL DEFAULT 0")
    if "explore_rift_rebirth_options_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_options_msg_id INTEGER NOT NULL DEFAULT 0")
    if "explore_rift_rebirth_select_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_select_msg_id INTEGER NOT NULL DEFAULT 0")
    if "explore_rift_rebirth_options_text" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_options_text TEXT NOT NULL DEFAULT ''")
    if "explore_rift_rebirth_selected_index" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_selected_index INTEGER NOT NULL DEFAULT 0")
    if "explore_rift_rebirth_last_result" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_last_result TEXT NOT NULL DEFAULT ''")
    if "explore_rift_rebirth_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_last_error TEXT NOT NULL DEFAULT ''")
    if "explore_rift_rebirth_choice_mode" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_choice_mode TEXT NOT NULL DEFAULT 'safe_first'")
    if "explore_rift_rebirth_preferred_root_type" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_preferred_root_type TEXT NOT NULL DEFAULT ''")
    if "explore_rift_rebirth_preferred_attrs" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_preferred_attrs TEXT NOT NULL DEFAULT ''")
    if "explore_rift_rebirth_blind_index" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_rebirth_blind_index INTEGER NOT NULL DEFAULT 1")
    if "explore_rift_fatal_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_fatal_msg_id INTEGER NOT NULL DEFAULT 0")
    if "explore_rift_fatal_confirm_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN explore_rift_fatal_confirm_due_at REAL NOT NULL DEFAULT 0")

    pending_columns = {row[1] for row in conn.execute("PRAGMA table_info(pending_tasks)").fetchall()}
    if "max_retry" not in pending_columns:
        conn.execute(f"ALTER TABLE pending_tasks ADD COLUMN max_retry INTEGER NOT NULL DEFAULT {int(RETRY_LIMIT)}")
    if "priority" not in pending_columns:
        conn.execute("ALTER TABLE pending_tasks ADD COLUMN priority TEXT NOT NULL DEFAULT ''")
    if "source_module" not in pending_columns:
        conn.execute("ALTER TABLE pending_tasks ADD COLUMN source_module TEXT NOT NULL DEFAULT ''")
    if "op_id" not in pending_columns:
        conn.execute("ALTER TABLE pending_tasks ADD COLUMN op_id TEXT NOT NULL DEFAULT ''")
    if "chain_id" not in pending_columns:
        conn.execute("ALTER TABLE pending_tasks ADD COLUMN chain_id TEXT NOT NULL DEFAULT ''")
    if "delete_policy" not in pending_columns:
        conn.execute("ALTER TABLE pending_tasks ADD COLUMN delete_policy TEXT NOT NULL DEFAULT ''")
    if "identity_info_reply_msg_ids" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN identity_info_reply_msg_ids TEXT NOT NULL DEFAULT '[]'")
    if "last_identity_info_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN last_identity_info_msg_id INTEGER NOT NULL DEFAULT 0")
    if "identity_info_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN identity_info_last_error TEXT NOT NULL DEFAULT ''")
    if "identity_info_last_requested_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN identity_info_last_requested_at REAL NOT NULL DEFAULT 0")
    if "identity_info_followup_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN identity_info_followup_due_at REAL NOT NULL DEFAULT 0")
    if "identity_info_primary_payload" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN identity_info_primary_payload TEXT NOT NULL DEFAULT '{}' ")

    # 第二元神模块
    if "second_soul_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN second_soul_enabled INTEGER NOT NULL DEFAULT 0")
    if "second_soul_auto_choice_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN second_soul_auto_choice_enabled INTEGER NOT NULL DEFAULT 1")
    if "second_soul_phase" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_phase TEXT NOT NULL DEFAULT 'idle'")
    if "second_soul_choice_strategy" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_choice_strategy TEXT NOT NULL DEFAULT 'stable'")
    if "second_soul_heart_demon_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_heart_demon_msg_id INTEGER NOT NULL DEFAULT 0")
    if "second_soul_heart_demon_notified" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_heart_demon_notified INTEGER NOT NULL DEFAULT 0")
    if "second_soul_status_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_status_msg_id INTEGER NOT NULL DEFAULT 0")
    if "second_soul_train_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_train_msg_id INTEGER NOT NULL DEFAULT 0")
    if "second_soul_last_train_started_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_last_train_started_at REAL NOT NULL DEFAULT 0")
    if "second_soul_last_broadcast_key" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_last_broadcast_key TEXT NOT NULL DEFAULT ''")
    if "second_soul_last_broadcast_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_last_broadcast_at REAL NOT NULL DEFAULT 0")
    if "second_soul_moran_value" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_moran_value INTEGER NOT NULL DEFAULT 0")
    if "second_soul_purge_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_purge_msg_id INTEGER NOT NULL DEFAULT 0")
    if "second_soul_purge_status_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_purge_status_msg_id INTEGER NOT NULL DEFAULT 0")
    if "second_soul_purge_attempts" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_purge_attempts INTEGER NOT NULL DEFAULT 0")
    if "second_soul_purge_due_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_purge_due_at REAL NOT NULL DEFAULT 0")
    if "second_soul_purge_last_at" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_purge_last_at REAL NOT NULL DEFAULT 0")
    if "second_soul_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN second_soul_last_error TEXT NOT NULL DEFAULT ''")
    if "next_second_soul_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_second_soul_time REAL NOT NULL DEFAULT 0")
    if "second_soul_heart_demon_deadline" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN second_soul_heart_demon_deadline REAL NOT NULL DEFAULT 0")

    # 太一门模块
    if "taiyi_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN taiyi_enabled INTEGER NOT NULL DEFAULT 0")
    if "taiyi_node_search_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN taiyi_node_search_enabled INTEGER NOT NULL DEFAULT 0")
    if "taiyi_yindao_element" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN taiyi_yindao_element TEXT NOT NULL DEFAULT '水'")
    if "taiyi_phase" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN taiyi_phase TEXT NOT NULL DEFAULT 'idle'")
    if "taiyi_pending_node_name" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN taiyi_pending_node_name TEXT NOT NULL DEFAULT ''")
    if "taiyi_yindao_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN taiyi_yindao_msg_id INTEGER NOT NULL DEFAULT 0")
    if "taiyi_node_search_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN taiyi_node_search_msg_id INTEGER NOT NULL DEFAULT 0")
    if "taiyi_node_define_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN taiyi_node_define_msg_id INTEGER NOT NULL DEFAULT 0")
    if "taiyi_freeze_reason" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN taiyi_freeze_reason TEXT NOT NULL DEFAULT ''")
    if "taiyi_failure_history" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN taiyi_failure_history TEXT NOT NULL DEFAULT '[]'")
    if "taiyi_yindao_resend_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN taiyi_yindao_resend_count INTEGER NOT NULL DEFAULT 0")
    if "taiyi_search_resend_count" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN taiyi_search_resend_count INTEGER NOT NULL DEFAULT 0")
    if "taiyi_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN taiyi_last_error TEXT NOT NULL DEFAULT ''")
    if "next_taiyi_cycle_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_taiyi_cycle_time REAL NOT NULL DEFAULT 0")
    if "taiyi_phase_entered_at" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN taiyi_phase_entered_at REAL NOT NULL DEFAULT 0")
    if "taiyi_freeze_until" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN taiyi_freeze_until REAL NOT NULL DEFAULT 0")
    _mark_schema_columns_ensured(conn)



def _normalize_small_world_preach_defaults(conn):
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?",
        (SMALL_WORLD_PREACH_DEFAULT_NORMALIZED_KEY,),
    ).fetchone()
    if row and str(row["value"] or "") == "1":
        return

    conn.execute(
        """
        UPDATE identity_module_state
        SET small_world_preach_enabled = 0
        WHERE small_world_preach_enabled = 1
          AND small_world_enabled = 0
          AND small_world_manifest_enabled = 0
          AND small_world_harvest_enabled = 0
          AND small_world_refine_enabled = 0
          AND small_world_refresh_enabled = 0
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (SMALL_WORLD_PREACH_DEFAULT_NORMALIZED_KEY, "1"),
    )


def _migrate_schema_to_current(conn):
    _ensure_schema_columns(conn)
    _normalize_small_world_preach_defaults(conn)
    conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        ("schema_version", str(DB_SCHEMA_VERSION)),
    )



def init_db():
    global _db_initialized
    if _db_initialized:
        return
    conn = get_db_conn()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS identities (
            send_as_id INTEGER PRIMARY KEY,
            username TEXT NOT NULL DEFAULT '',
            label TEXT NOT NULL DEFAULT '',
            daohao TEXT NOT NULL DEFAULT '',
            realm TEXT NOT NULL DEFAULT '',
            spiritual_root_type TEXT NOT NULL DEFAULT '',
            spiritual_root_attrs TEXT NOT NULL DEFAULT '',
            replica_professions TEXT NOT NULL DEFAULT '',
            replica_gold_dps_enabled INTEGER NOT NULL DEFAULT 0,
            pet_name TEXT NOT NULL DEFAULT '',
            pet_warm_name TEXT NOT NULL DEFAULT '',
            pet_trial_name TEXT NOT NULL DEFAULT '',
            sect_name TEXT NOT NULL DEFAULT '',
            sect_updated_at REAL NOT NULL DEFAULT 0,
            jiyin_choice TEXT NOT NULL DEFAULT '',
            nanlong_choice TEXT NOT NULL DEFAULT 'reject',
            stargazer_star_choice TEXT NOT NULL DEFAULT '赤血星',
            tianti_rank_choice TEXT NOT NULL DEFAULT '普通',
            stargazer_total_slots INTEGER NOT NULL DEFAULT 0,
            checkin_window_start_hour_utc INTEGER NOT NULL DEFAULT 2,
            checkin_window_end_hour_utc INTEGER NOT NULL DEFAULT 3,
            tower_window_start_hour_utc INTEGER NOT NULL DEFAULT 1,
            tower_window_end_hour_utc INTEGER NOT NULL DEFAULT 2,
            enabled INTEGER NOT NULL DEFAULT 1,
            xiuwei_current INTEGER NOT NULL DEFAULT 0,
            xiuwei_max INTEGER NOT NULL DEFAULT 0,
            battle_power_text TEXT NOT NULL DEFAULT '',
            battle_power_value INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS identity_module_state (
            send_as_id INTEGER PRIMARY KEY,
            tree_enabled INTEGER NOT NULL,
            pet_enabled INTEGER NOT NULL,
            pet_warm_enabled INTEGER NOT NULL DEFAULT 0,
            pet_trial_enabled INTEGER NOT NULL DEFAULT 0,
            ranch_enabled INTEGER NOT NULL DEFAULT 0,
            wild_training_enabled INTEGER NOT NULL DEFAULT 0,
            stargazer_enabled INTEGER NOT NULL DEFAULT 0,
            guanxing_monitor_enabled INTEGER NOT NULL DEFAULT 0,
            guanxing_enabled INTEGER NOT NULL DEFAULT 0,
            formation_enabled INTEGER NOT NULL DEFAULT 0,
            tianti_enabled INTEGER NOT NULL DEFAULT 0,
            tianti_wenxin_enabled INTEGER NOT NULL DEFAULT 1,
            tianti_gangfeng_enabled INTEGER NOT NULL DEFAULT 1,
            quiz_enabled INTEGER NOT NULL,
            jiyin_enabled INTEGER NOT NULL DEFAULT 0,
            concubine_enabled INTEGER NOT NULL DEFAULT 0,
            concubine_tianji_enabled INTEGER NOT NULL DEFAULT 0,
            concubine_heart_enabled INTEGER NOT NULL DEFAULT 0,
            concubine_voyage_enabled INTEGER NOT NULL DEFAULT 0,
            concubine_auto_reacquire INTEGER NOT NULL DEFAULT 1,
            hehuan_enabled INTEGER NOT NULL DEFAULT 0,
            tianxing_enabled INTEGER NOT NULL DEFAULT 0,
            yinluo_enabled INTEGER NOT NULL DEFAULT 0,
            world_boss_enabled INTEGER NOT NULL DEFAULT 0,
            nanlong_enabled INTEGER NOT NULL DEFAULT 0,
            explore_rift_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_preach_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_manifest_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_harvest_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_refine_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_refresh_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_barrier_enabled INTEGER NOT NULL DEFAULT 1,
            small_world_barrier_min_stock INTEGER NOT NULL DEFAULT 130000,
            small_world_barrier_guard_before_min INTEGER NOT NULL DEFAULT 30,
            small_world_barrier_min_interval_hours REAL NOT NULL DEFAULT 18,
            divination_enabled INTEGER NOT NULL DEFAULT 0,
            divination_daily_limit INTEGER NOT NULL DEFAULT 6,
            dungeon_join_enabled INTEGER NOT NULL DEFAULT 0,
            second_soul_enabled INTEGER NOT NULL DEFAULT 0,
            second_soul_auto_choice_enabled INTEGER NOT NULL DEFAULT 1,
            wendao_enabled INTEGER NOT NULL DEFAULT 0,
            duel_enabled INTEGER NOT NULL DEFAULT 0,
            fishing_enabled INTEGER NOT NULL DEFAULT 0,
            yuanying_enabled INTEGER NOT NULL,
            deep_retreat_enabled INTEGER NOT NULL,
            checkin_enabled INTEGER NOT NULL,
            sect_teach_enabled INTEGER NOT NULL DEFAULT 0,
            tower_enabled INTEGER NOT NULL,
            is_maturing INTEGER NOT NULL,
            is_invading INTEGER NOT NULL,
            is_harvested INTEGER NOT NULL,
            pending_irrigation INTEGER NOT NULL,
            tree_bootstrap_check_needed INTEGER NOT NULL,
            checkin_teach_count INTEGER NOT NULL,
            checkin_teach_day TEXT NOT NULL,
            last_checkin_done_day TEXT NOT NULL,
            last_tower_day TEXT NOT NULL,
            last_guanxing_done_day TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS identity_timers (
            send_as_id INTEGER PRIMARY KEY,
            next_irr_time REAL NOT NULL,
            next_guard_time REAL NOT NULL,
            next_pet_time REAL NOT NULL,
            next_pet_warm_time REAL NOT NULL DEFAULT 0,
            next_pet_trial_time REAL NOT NULL DEFAULT 0,
            next_ranch_time REAL NOT NULL DEFAULT 0,
            next_wild_training_time REAL NOT NULL DEFAULT 0,
            next_stargazer_panel_time REAL NOT NULL DEFAULT 0,
            stargazer_collect_due_at REAL NOT NULL DEFAULT 0,
            next_guanxing_monitor_notify_time REAL NOT NULL DEFAULT 0,
            next_tianti_status_time REAL NOT NULL DEFAULT 0,
            next_tianti_wenxin_time REAL NOT NULL DEFAULT 0,
            next_tianti_climb_time REAL NOT NULL DEFAULT 0,
            next_tianti_gangfeng_time REAL NOT NULL DEFAULT 0,
            next_checkin_time REAL NOT NULL,
            next_sect_teach_time REAL NOT NULL,
            next_tower_time REAL NOT NULL,
            next_quiz_time REAL NOT NULL,
            next_jiyin_time REAL NOT NULL,
            next_concubine_time REAL NOT NULL DEFAULT 0,
            next_nanlong_time REAL NOT NULL DEFAULT 0,
            next_small_world_time REAL NOT NULL DEFAULT 0,
            next_yuanying_time REAL NOT NULL,
            next_explore_rift_time REAL NOT NULL DEFAULT 0,
            next_wendao_time REAL NOT NULL DEFAULT 0,
            next_duel_time REAL NOT NULL DEFAULT 0,
            next_fishing_time REAL NOT NULL DEFAULT 0,
            next_formation_time REAL NOT NULL DEFAULT 0,
            formation_cooldown_until REAL NOT NULL DEFAULT 0,
            next_deep_retreat_time REAL NOT NULL,
            weak_until REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS identity_runtime_state (
            send_as_id INTEGER PRIMARY KEY,
            weak_reason TEXT NOT NULL DEFAULT '',
            weak_source TEXT NOT NULL DEFAULT '',
            weak_last_block_log_at REAL NOT NULL DEFAULT 0,
            sect_teach_reply_to_msg_id INTEGER NOT NULL,
            last_checkin_msg_id INTEGER NOT NULL,
            last_sect_teach_msg_id INTEGER NOT NULL,
            checkin_cleanup_msg_ids TEXT NOT NULL,
            tree_maturing_logged INTEGER NOT NULL DEFAULT 0,
            tree_harvest_followup_due_at REAL NOT NULL DEFAULT 0,
            tree_harvest_inflight_until REAL NOT NULL DEFAULT 0,
            tree_last_harvest_result_msg_id INTEGER NOT NULL DEFAULT 0,
            tree_last_harvest_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            tree_bootstrap_check_due_at REAL NOT NULL DEFAULT 0,
            last_tree_status_sent_at REAL NOT NULL DEFAULT 0,
            tree_pulse_mode_seen INTEGER NOT NULL DEFAULT 0,
            tree_pulse_last_panel_at REAL NOT NULL DEFAULT 0,
            tree_pulse_progress REAL NOT NULL DEFAULT 0,
            tree_pulse_main TEXT NOT NULL DEFAULT '',
            tree_pulse_aux TEXT NOT NULL DEFAULT '',
            tree_pulse_reverse TEXT NOT NULL DEFAULT '',
            tree_pulse_neutral TEXT NOT NULL DEFAULT '',
            tree_pulse_stability INTEGER NOT NULL DEFAULT 0,
            tree_pulse_stability_max INTEGER NOT NULL DEFAULT 0,
            tree_pulse_turbidity INTEGER NOT NULL DEFAULT 0,
            tree_pulse_turbidity_max INTEGER NOT NULL DEFAULT 0,
            tree_pulse_daily_used INTEGER NOT NULL DEFAULT 0,
            tree_pulse_daily_limit INTEGER NOT NULL DEFAULT 0,
            tree_pulse_rush_used INTEGER NOT NULL DEFAULT 0,
            tree_pulse_rush_limit INTEGER NOT NULL DEFAULT 0,
            tree_pulse_last_action TEXT NOT NULL DEFAULT '',
            tree_pulse_last_error TEXT NOT NULL DEFAULT '',
            tree_pulse_blocked_until REAL NOT NULL DEFAULT 0,
            last_tower_msg_id INTEGER NOT NULL,
            last_tower_command_sent_at REAL NOT NULL DEFAULT 0,
            tower_reply_due_at REAL NOT NULL DEFAULT 0,
            tower_retry_count INTEGER NOT NULL DEFAULT 0,
            pet_last_error TEXT NOT NULL DEFAULT '',
            pet_warm_last_error TEXT NOT NULL DEFAULT '',
            pet_trial_last_error TEXT NOT NULL DEFAULT '',
            ranch_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            ranch_reply_due_at REAL NOT NULL DEFAULT 0,
            ranch_retry_count INTEGER NOT NULL DEFAULT 0,
            ranch_last_msg_id INTEGER NOT NULL DEFAULT 0,
            ranch_last_result TEXT NOT NULL DEFAULT '',
            ranch_last_error TEXT NOT NULL DEFAULT '',
            ranch_return_pending INTEGER NOT NULL DEFAULT 0,
            ranch_return_seen_msg_id INTEGER NOT NULL DEFAULT 0,
            ranch_return_wait_since REAL NOT NULL DEFAULT 0,
            ranch_return_last_notified_at REAL NOT NULL DEFAULT 0,
            wild_training_strategy TEXT NOT NULL DEFAULT '深入',
            wild_training_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            wild_training_reply_due_at REAL NOT NULL DEFAULT 0,
            wild_training_retry_count INTEGER NOT NULL DEFAULT 0,
            wild_training_last_msg_id INTEGER NOT NULL DEFAULT 0,
            wild_training_last_result TEXT NOT NULL DEFAULT '',
            wild_training_last_result_at REAL NOT NULL DEFAULT 0,
            wild_training_last_error TEXT NOT NULL DEFAULT '',
            stargazer_last_panel_msg_id INTEGER NOT NULL DEFAULT 0,
            stargazer_last_action TEXT NOT NULL DEFAULT '',
            stargazer_queued_action TEXT NOT NULL DEFAULT '',
            stargazer_idle_slot_count INTEGER NOT NULL DEFAULT 0,
            stargazer_dim_slot_count INTEGER NOT NULL DEFAULT 0,
            stargazer_ready_slot_count INTEGER NOT NULL DEFAULT 0,
            stargazer_busy_until REAL NOT NULL DEFAULT 0,
            stargazer_followup_due_at REAL NOT NULL DEFAULT 0,
            stargazer_wait_full_collect INTEGER NOT NULL DEFAULT 0,
            stargazer_collect_ready INTEGER NOT NULL DEFAULT 0,
            stargazer_soothe_before_collect INTEGER NOT NULL DEFAULT 0,
            guanxing_last_query_msg_id INTEGER NOT NULL DEFAULT 0,
            guanxing_last_panel_msg_id INTEGER NOT NULL DEFAULT 0,
            guanxing_panel_slot_key TEXT NOT NULL DEFAULT '',
            guanxing_last_panel_seen_at REAL NOT NULL DEFAULT 0,
            guanxing_last_shift_msg_id INTEGER NOT NULL DEFAULT 0,
            guanxing_last_shift_slot_key TEXT NOT NULL DEFAULT '',
            guanxing_last_shift_target TEXT NOT NULL DEFAULT '',
            guanxing_last_error TEXT NOT NULL DEFAULT '',
            last_formation_msg_id INTEGER NOT NULL DEFAULT 0,
            formation_pending_invite_msg_id INTEGER NOT NULL DEFAULT 0,
            formation_pending_assist_msg_id INTEGER NOT NULL DEFAULT 0,
            formation_last_action TEXT NOT NULL DEFAULT '',
            formation_last_result TEXT NOT NULL DEFAULT '',
            formation_last_error TEXT NOT NULL DEFAULT '',
            formation_last_success_at REAL NOT NULL DEFAULT 0,
            tianti_status_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            tianti_last_status_msg_id INTEGER NOT NULL DEFAULT 0,
            tianti_last_status_seen_at REAL NOT NULL DEFAULT 0,
            tianti_last_wenxin_msg_id INTEGER NOT NULL DEFAULT 0,
            tianti_last_climb_msg_id INTEGER NOT NULL DEFAULT 0,
            tianti_last_gangfeng_msg_id INTEGER NOT NULL DEFAULT 0,
            tianti_progress_current INTEGER NOT NULL DEFAULT 0,
            tianti_progress_total INTEGER NOT NULL DEFAULT 12,
            tianti_cycle_count INTEGER NOT NULL DEFAULT 0,
            tianti_gangfeng_level INTEGER NOT NULL DEFAULT 0,
            tianti_gangfeng_total INTEGER NOT NULL DEFAULT 12,
            tianti_cooldown_text TEXT NOT NULL DEFAULT '未记录',
            tianti_wenxin_status TEXT NOT NULL DEFAULT '未记录',
            tianti_gangfeng_status TEXT NOT NULL DEFAULT '未记录',
            tianti_remaining_climb_count INTEGER NOT NULL DEFAULT 0,
            tianti_last_wenxin_day TEXT NOT NULL DEFAULT '',
            tianti_wenxin_last_trigger_key TEXT NOT NULL DEFAULT '',
            tianti_gangfeng_last_trigger_key TEXT NOT NULL DEFAULT '',
            tianti_last_skip_reason TEXT NOT NULL DEFAULT '',
            tianti_theoretical_max_stage INTEGER NOT NULL DEFAULT 0,
            tianti_wenxin_trigger_stage INTEGER NOT NULL DEFAULT 0,
            tianti_last_cost_xiuwei INTEGER NOT NULL DEFAULT 0,
            tianti_last_gain_xiuwei INTEGER NOT NULL DEFAULT 0,
            tianti_last_gain_contrib INTEGER NOT NULL DEFAULT 0,
            tianti_last_error TEXT NOT NULL DEFAULT '',
            guanxing_monitor_slot_key TEXT NOT NULL DEFAULT '',
            guanxing_monitor_slot_start_at REAL NOT NULL DEFAULT 0,
            guanxing_monitor_slot_end_at REAL NOT NULL DEFAULT 0,
            guanxing_monitor_seen_panel INTEGER NOT NULL DEFAULT 0,
            guanxing_monitor_matched_keyword TEXT NOT NULL DEFAULT '',
            guanxing_monitor_matched_value TEXT NOT NULL DEFAULT '',
            guanxing_monitor_last_evolution_value TEXT NOT NULL DEFAULT '',
            guanxing_monitor_last_seen_at REAL NOT NULL DEFAULT 0,
            guanxing_monitor_last_notified_slot_key TEXT NOT NULL DEFAULT '',
            quiz_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            quiz_chat_id INTEGER NOT NULL DEFAULT 0,
            quiz_question TEXT NOT NULL DEFAULT '',
            quiz_options TEXT NOT NULL DEFAULT '{}',
            quiz_answer TEXT NOT NULL DEFAULT '',
            quiz_phase TEXT NOT NULL DEFAULT '',
            quiz_retry_count INTEGER NOT NULL DEFAULT 0,
            quiz_match_mode TEXT NOT NULL DEFAULT '',
            quiz_answer_method TEXT NOT NULL DEFAULT '',
            quiz_last_error TEXT NOT NULL DEFAULT '',
            quiz_last_matched_at REAL NOT NULL DEFAULT 0,
            jiyin_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            jiyin_last_error TEXT NOT NULL DEFAULT '',
            concubine_phase TEXT NOT NULL DEFAULT 'idle',
            concubine_availability TEXT NOT NULL DEFAULT 'unknown',
            concubine_nanlong_strategy TEXT NOT NULL DEFAULT 'reacquire_after_loss',
            concubine_status_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_greet_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_last_greet_day TEXT NOT NULL DEFAULT '',
            concubine_greet_retry_count INTEGER NOT NULL DEFAULT 0,
            concubine_greet_last_error TEXT NOT NULL DEFAULT '',
            concubine_gift_status_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_gift_bag_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_gift_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_gift_amount INTEGER NOT NULL DEFAULT 0,
            concubine_last_gift_day TEXT NOT NULL DEFAULT '',
            concubine_gift_attempt_day TEXT NOT NULL DEFAULT '',
            concubine_gift_last_error TEXT NOT NULL DEFAULT '',
            concubine_dream_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_fragment_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_puzzle_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_reacquire_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_tianji_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_heart_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_heart_prompt_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_voyage_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_voyage_retry_count INTEGER NOT NULL DEFAULT 0,
            concubine_last_panel_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_name TEXT NOT NULL DEFAULT '',
            concubine_kind TEXT NOT NULL DEFAULT '',
            concubine_location TEXT NOT NULL DEFAULT '',
            concubine_affinity INTEGER NOT NULL DEFAULT 0,
            concubine_oath TEXT NOT NULL DEFAULT '',
            concubine_dream_due_at REAL NOT NULL DEFAULT 0,
            concubine_tianji_due_at REAL NOT NULL DEFAULT 0,
            concubine_heart_due_at REAL NOT NULL DEFAULT 0,
            concubine_tianji_chain TEXT NOT NULL DEFAULT '',
            concubine_tianji_chain_due_at REAL NOT NULL DEFAULT 0,
            concubine_heart_round INTEGER NOT NULL DEFAULT 0,
            concubine_heart_choice_prompt_msg_id INTEGER NOT NULL DEFAULT 0,
            concubine_heart_choice_round INTEGER NOT NULL DEFAULT 0,
            concubine_heart_choice_sent_at REAL NOT NULL DEFAULT 0,
            concubine_heart_choice_retry_count INTEGER NOT NULL DEFAULT 0,
            concubine_last_recovered_reply_key TEXT NOT NULL DEFAULT '',
            concubine_last_recovered_reply_at REAL NOT NULL DEFAULT 0,
            concubine_fragment_count INTEGER NOT NULL DEFAULT 0,
            concubine_fragment_total INTEGER NOT NULL DEFAULT 4,
            concubine_fragment_xutian_count INTEGER NOT NULL DEFAULT 0,
            concubine_fragment_xutian_total INTEGER NOT NULL DEFAULT 4,
            concubine_fragment_cangkun_count INTEGER NOT NULL DEFAULT 0,
            concubine_fragment_cangkun_total INTEGER NOT NULL DEFAULT 4,
            concubine_fragment_confirm_key TEXT NOT NULL DEFAULT '',
            concubine_fragment_confirmed_at REAL NOT NULL DEFAULT 0,
            concubine_voyage_status TEXT NOT NULL DEFAULT '',
            concubine_voyage_route TEXT NOT NULL DEFAULT '',
            concubine_voyage_return_at REAL NOT NULL DEFAULT 0,
            concubine_voyage_last_result TEXT NOT NULL DEFAULT '',
            concubine_voyage_last_error TEXT NOT NULL DEFAULT '',
            concubine_last_snapshot_at REAL NOT NULL DEFAULT 0,
            concubine_reacquire_blocked_until REAL NOT NULL DEFAULT 0,
            concubine_reacquire_attempts INTEGER NOT NULL DEFAULT 0,
            concubine_reacquire_command_override TEXT NOT NULL DEFAULT '',
            concubine_last_error TEXT NOT NULL DEFAULT '',
            concubine_tianji_last_error TEXT NOT NULL DEFAULT '',
            concubine_heart_last_error TEXT NOT NULL DEFAULT '',
            hehuan_observation TEXT NOT NULL DEFAULT '{}',
            tianxing_observation TEXT NOT NULL DEFAULT '{}',
            tianxing_auto_config TEXT NOT NULL DEFAULT '{}',
            tianxing_timeline_state TEXT NOT NULL DEFAULT '{}',
            yinluo_observation TEXT NOT NULL DEFAULT '{}',
            world_boss_action_count INTEGER NOT NULL DEFAULT 0,
            world_boss_action_limit INTEGER NOT NULL DEFAULT 5,
            world_boss_attack_count INTEGER NOT NULL DEFAULT 0,
            world_boss_pending_msg_id INTEGER NOT NULL DEFAULT 0,
            world_boss_pending_action TEXT NOT NULL DEFAULT '',
            world_boss_pending_since REAL NOT NULL DEFAULT 0,
            world_boss_pending_retry_count INTEGER NOT NULL DEFAULT 0,
            world_boss_pending_action_seq INTEGER NOT NULL DEFAULT 0,
            world_boss_last_action TEXT NOT NULL DEFAULT '',
            world_boss_last_action_at REAL NOT NULL DEFAULT 0,
            world_boss_last_reply_msg_id INTEGER NOT NULL DEFAULT 0,
            world_boss_exhausted INTEGER NOT NULL DEFAULT 0,
            world_boss_last_error TEXT NOT NULL DEFAULT '',
            nanlong_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            nanlong_reply_due_at REAL NOT NULL DEFAULT 0,
            nanlong_last_msg_id INTEGER NOT NULL DEFAULT 0,
            nanlong_retry_count INTEGER NOT NULL DEFAULT 0,
            nanlong_last_command TEXT NOT NULL DEFAULT '',
            nanlong_protect_phase TEXT NOT NULL DEFAULT '',
            nanlong_place_msg_id INTEGER NOT NULL DEFAULT 0,
            nanlong_recall_msg_id INTEGER NOT NULL DEFAULT 0,
            nanlong_last_error TEXT NOT NULL DEFAULT '',
            small_world_preach_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            small_world_preach_due_at REAL NOT NULL DEFAULT 0,
            small_world_god_cooldown_until REAL NOT NULL DEFAULT 0,
            small_world_pending_god_action TEXT NOT NULL DEFAULT '',
            small_world_pending_god_reason TEXT NOT NULL DEFAULT '',
            small_world_pending_god_priority INTEGER NOT NULL DEFAULT 0,
            small_world_pending_god_at REAL NOT NULL DEFAULT 0,
            small_world_last_god_action TEXT NOT NULL DEFAULT '',
            small_world_last_god_sent_at REAL NOT NULL DEFAULT 0,
            small_world_last_disaster_wave_at REAL NOT NULL DEFAULT 0,
            small_world_barrier_msg_id INTEGER NOT NULL DEFAULT 0,
            small_world_barrier_due_at REAL NOT NULL DEFAULT 0,
            small_world_last_barrier_sent_at REAL NOT NULL DEFAULT 0,
            small_world_phase TEXT NOT NULL DEFAULT 'idle',
            small_world_query_msg_id INTEGER NOT NULL DEFAULT 0,
            small_world_manifest_msg_id INTEGER NOT NULL DEFAULT 0,
            small_world_manifest_cost_text TEXT NOT NULL DEFAULT '',
            small_world_harvest_msg_id INTEGER NOT NULL DEFAULT 0,
            small_world_refine_msg_id INTEGER NOT NULL DEFAULT 0,
            small_world_refresh_count INTEGER NOT NULL DEFAULT 0,
            small_world_pending_incense REAL NOT NULL DEFAULT 0,
            small_world_incense_stock INTEGER NOT NULL DEFAULT 0,
            small_world_faith_value INTEGER NOT NULL DEFAULT 0,
            small_world_panel_snapshot TEXT NOT NULL DEFAULT '{}',
            small_world_last_panel_at REAL NOT NULL DEFAULT 0,
            small_world_last_error TEXT NOT NULL DEFAULT '',
            resource_shortage_backoffs TEXT NOT NULL DEFAULT '{}',
            action_guard_sessions TEXT NOT NULL DEFAULT '{}',
            yuanying_phase TEXT NOT NULL,
            yuanying_probe_pending INTEGER NOT NULL,
            yuanying_waiting_logged INTEGER NOT NULL DEFAULT 0,
            yuanying_protect_logged INTEGER NOT NULL DEFAULT 0,
            yuanying_summary_sent_at REAL NOT NULL,
            last_yuanying_summary_msg_id INTEGER NOT NULL,
            last_yuanying_command_time REAL NOT NULL,
            explore_rift_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            explore_rift_reply_due_at REAL NOT NULL DEFAULT 0,
            explore_rift_pending_result_msg_id INTEGER NOT NULL DEFAULT 0,
            explore_rift_last_msg_id INTEGER NOT NULL DEFAULT 0,
            explore_rift_last_result TEXT NOT NULL DEFAULT '',
            explore_rift_last_error TEXT NOT NULL DEFAULT '',
            explore_rift_last_result_key TEXT NOT NULL DEFAULT '',
            explore_rift_manual_required INTEGER NOT NULL DEFAULT 0,
            explore_rift_nascent_escape_weak_until REAL NOT NULL DEFAULT 0,
            explore_rift_rebirth_required INTEGER NOT NULL DEFAULT 0,
            explore_rift_rebirth_phase TEXT NOT NULL DEFAULT 'idle',
            explore_rift_rebirth_due_at REAL NOT NULL DEFAULT 0,
            explore_rift_rebirth_request_msg_id INTEGER NOT NULL DEFAULT 0,
            explore_rift_rebirth_options_msg_id INTEGER NOT NULL DEFAULT 0,
            explore_rift_rebirth_select_msg_id INTEGER NOT NULL DEFAULT 0,
            explore_rift_rebirth_options_text TEXT NOT NULL DEFAULT '',
            explore_rift_rebirth_selected_index INTEGER NOT NULL DEFAULT 0,
            explore_rift_rebirth_last_result TEXT NOT NULL DEFAULT '',
            explore_rift_rebirth_last_error TEXT NOT NULL DEFAULT '',
            explore_rift_fatal_msg_id INTEGER NOT NULL DEFAULT 0,
            explore_rift_fatal_confirm_due_at REAL NOT NULL DEFAULT 0,
            wendao_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            wendao_reply_due_at REAL NOT NULL DEFAULT 0,
            wendao_pending_result_msg_id INTEGER NOT NULL DEFAULT 0,
            wendao_sent_at REAL NOT NULL DEFAULT 0,
            wendao_last_msg_id INTEGER NOT NULL DEFAULT 0,
            wendao_last_result TEXT NOT NULL DEFAULT '',
            wendao_last_error TEXT NOT NULL DEFAULT '',
            duel_target TEXT NOT NULL DEFAULT '',
            duel_total_count INTEGER NOT NULL DEFAULT 0,
            duel_completed_count INTEGER NOT NULL DEFAULT 0,
            duel_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            duel_reply_due_at REAL NOT NULL DEFAULT 0,
            duel_open_msg_id INTEGER NOT NULL DEFAULT 0,
            duel_magic_due_at REAL NOT NULL DEFAULT 0,
            duel_magic_sent_at REAL NOT NULL DEFAULT 0,
            duel_started_at REAL NOT NULL DEFAULT 0,
            duel_last_msg_id INTEGER NOT NULL DEFAULT 0,
            duel_last_result TEXT NOT NULL DEFAULT '',
            duel_last_error TEXT NOT NULL DEFAULT '',
            fishing_enabled INTEGER NOT NULL DEFAULT 0,
            next_fishing_time REAL NOT NULL DEFAULT 0,
            fishing_pond TEXT NOT NULL DEFAULT '青溪浅滩',
            fishing_bait TEXT NOT NULL DEFAULT '凡饵',
            fishing_daily_limit INTEGER NOT NULL DEFAULT 20,
            fishing_daily_day TEXT NOT NULL DEFAULT '',
            fishing_daily_count INTEGER NOT NULL DEFAULT 0,
            fishing_basket_calibrated_day TEXT NOT NULL DEFAULT '',
            fishing_auto_chum_enabled INTEGER NOT NULL DEFAULT 1,
            fishing_chum_name TEXT NOT NULL DEFAULT '',
            fishing_chum_names TEXT NOT NULL DEFAULT '',
            fishing_chum_day TEXT NOT NULL DEFAULT '',
            fishing_chum_counts TEXT NOT NULL DEFAULT '',
            fishing_auto_buy_bait_enabled INTEGER NOT NULL DEFAULT 1,
            fishing_auto_buy_bait_count INTEGER NOT NULL DEFAULT 20,
            fishing_auto_probe_enabled INTEGER NOT NULL DEFAULT 0,
            fishing_auto_open_fish_enabled INTEGER NOT NULL DEFAULT 1,
            fishing_cancel_after_sec INTEGER NOT NULL DEFAULT 120,
            fishing_transfer_target_id INTEGER NOT NULL DEFAULT 0,
            fishing_transfer_due_at REAL NOT NULL DEFAULT 0,
            fishing_caught_fish_json TEXT NOT NULL DEFAULT '',
            fishing_valuable_drop_reminders TEXT NOT NULL DEFAULT '[]',
            fishing_phase TEXT NOT NULL DEFAULT 'idle',
            fishing_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            fishing_reply_due_at REAL NOT NULL DEFAULT 0,
            fishing_status_msg_id INTEGER NOT NULL DEFAULT 0,
            fishing_pending_action TEXT NOT NULL DEFAULT '',
            fishing_pending_open_fish TEXT NOT NULL DEFAULT '',
            fishing_forced_buy_bait TEXT NOT NULL DEFAULT '',
            fishing_forced_buy_count INTEGER NOT NULL DEFAULT 0,
            fishing_started_at REAL NOT NULL DEFAULT 0,
            fishing_active_chum_name TEXT NOT NULL DEFAULT '',
            fishing_chum_rods_remaining INTEGER NOT NULL DEFAULT 0,
            fishing_last_msg_id INTEGER NOT NULL DEFAULT 0,
            fishing_last_result TEXT NOT NULL DEFAULT '',
            fishing_last_error TEXT NOT NULL DEFAULT '',
            deep_retreat_phase TEXT NOT NULL,
            deep_retreat_probe_pending INTEGER NOT NULL,
            deep_retreat_waiting_logged INTEGER NOT NULL DEFAULT 0,
            deep_retreat_protect_logged INTEGER NOT NULL DEFAULT 0,
            deep_retreat_summary_sent_at REAL NOT NULL,
            last_deep_retreat_summary_msg_id INTEGER NOT NULL,
            last_deep_retreat_command_time REAL NOT NULL,
            second_soul_phase TEXT NOT NULL DEFAULT 'idle',
            second_soul_choice_strategy TEXT NOT NULL DEFAULT 'stable',
            second_soul_heart_demon_msg_id INTEGER NOT NULL DEFAULT 0,
            second_soul_heart_demon_notified INTEGER NOT NULL DEFAULT 0,
            second_soul_status_msg_id INTEGER NOT NULL DEFAULT 0,
            second_soul_train_msg_id INTEGER NOT NULL DEFAULT 0,
            second_soul_last_train_started_at REAL NOT NULL DEFAULT 0,
            second_soul_last_broadcast_key TEXT NOT NULL DEFAULT '',
            second_soul_last_broadcast_at REAL NOT NULL DEFAULT 0,
            second_soul_moran_value INTEGER NOT NULL DEFAULT 0,
            second_soul_purge_msg_id INTEGER NOT NULL DEFAULT 0,
            second_soul_purge_status_msg_id INTEGER NOT NULL DEFAULT 0,
            second_soul_purge_attempts INTEGER NOT NULL DEFAULT 0,
            second_soul_purge_due_at REAL NOT NULL DEFAULT 0,
            second_soul_purge_last_at REAL NOT NULL DEFAULT 0,
            second_soul_last_error TEXT NOT NULL DEFAULT '',
            identity_info_reply_msg_ids TEXT NOT NULL DEFAULT '[]',
            last_identity_info_msg_id INTEGER NOT NULL DEFAULT 0,
            identity_info_last_error TEXT NOT NULL DEFAULT '',
            identity_info_last_requested_at REAL NOT NULL DEFAULT 0,
            identity_info_followup_due_at REAL NOT NULL DEFAULT 0,
            identity_info_primary_payload TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS pending_tasks (
            msg_id INTEGER PRIMARY KEY,
            send_as_id INTEGER NOT NULL,
            cmd TEXT NOT NULL,
            sent_at REAL NOT NULL,
            retry INTEGER NOT NULL,
            timeout REAL NOT NULL,
            reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            max_retry INTEGER NOT NULL DEFAULT 1,
            priority TEXT NOT NULL DEFAULT '',
            source_module TEXT NOT NULL DEFAULT '',
            op_id TEXT NOT NULL DEFAULT '',
            chain_id TEXT NOT NULL DEFAULT '',
            delete_policy TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS message_index (
            msg_id INTEGER PRIMARY KEY,
            send_as_id INTEGER NOT NULL,
            sent_at REAL NOT NULL,
            kind TEXT NOT NULL DEFAULT 'command'
        );

        CREATE TABLE IF NOT EXISTS official_schedule_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            send_as_id INTEGER NOT NULL,
            template_key TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            anchor_at REAL NOT NULL DEFAULT 0,
            horizon_days INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL DEFAULT '',
            options_json TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS official_scheduled_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL DEFAULT 0,
            send_as_id INTEGER NOT NULL,
            template_key TEXT NOT NULL DEFAULT '',
            command TEXT NOT NULL,
            schedule_at REAL NOT NULL,
            scheduled_msg_id INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'planned',
            source TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_official_schedule_batches_identity_template
            ON official_schedule_batches(send_as_id, template_key, status);
        CREATE INDEX IF NOT EXISTS idx_official_scheduled_messages_batch
            ON official_scheduled_messages(batch_id);
        CREATE INDEX IF NOT EXISTS idx_official_scheduled_messages_identity_time
            ON official_scheduled_messages(send_as_id, schedule_at);
        """
    )
    current_schema_version = _get_schema_version(conn)
    if current_schema_version < DB_SCHEMA_VERSION:
        _migrate_schema_to_current(conn)
    else:
        _ensure_schema_columns(conn)
        _normalize_small_world_preach_defaults(conn)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("game_group_id", "-1001680975844"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("game_bot_ids", "[-1003983937918, 7900199668, 8349385938, 8388633812, 8400307678, 8547797815, 8567800706, 8609885831, 8757550896]"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("game_topic_id", "7310786"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("forum_topics", "[]"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("forum_topics_updated_at", "0"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("auto_delete_sent_messages", "1"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("global_enabled", "1"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("tiandao_judgement_enabled", "0"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("tiandao_judgement_pending", "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("tianji_quiz_pending", "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("divination_run_state", "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("world_boss_run_state", "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_monitor_enabled", "0"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_monitor_targets", _encode_meta_json(["地磁暴动", "星辰异象"])),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_shift_target", ""),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_shift_delay_sec", "10"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("next_guanxing_monitor_notify_time", "0"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_monitor_slot_key", ""),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_monitor_slot_start_at", "0"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_monitor_slot_end_at", "0"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_monitor_seen_panel", "0"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_monitor_matched_keyword", ""),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_monitor_matched_value", ""),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_monitor_last_evolution_value", ""),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_monitor_last_seen_at", "0"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_monitor_last_notified_slot_key", ""),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("guanxing_round_state", "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("formation_run_state", "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("quiz_learning_watchers", "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("quiz_ai_config", "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("accounts", "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("identity_account_map", "{}"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("identity_membership_initialized", "0"),
    )
    conn.commit()
    _db_initialized = True


def _serialize_db_value(key, value):
    if key in IDENTITY_JSON_COLUMNS:
        if value is None:
            default_value = IDENTITY_STATE_TEMPLATE.get(key)
            value = {} if isinstance(default_value, dict) else []
        return json.dumps(value, ensure_ascii=False)
    if key in IDENTITY_BOOL_FIELDS:
        return 1 if value else 0
    return value


def _deserialize_db_value(key, value):
    default_value = IDENTITY_STATE_TEMPLATE.get(key)
    if value is None:
        if isinstance(default_value, list):
            return []
        if isinstance(default_value, dict):
            return {}
        return default_value
    if key in IDENTITY_JSON_COLUMNS:
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = None
        if isinstance(default_value, dict):
            return parsed if isinstance(parsed, dict) else {}
        if isinstance(default_value, list):
            return parsed if isinstance(parsed, list) else []
        return parsed
    if key in IDENTITY_BOOL_FIELDS:
        return bool(value)
    return value


def upsert_identity_to_db(send_as_id):
    conn = get_db_conn()
    _ensure_schema_columns_ready(conn)
    identity_state = get_identity_state(send_as_id)
    profile = get_send_as_profile(send_as_id)
    now_ts = time.time()

    conn.execute(
        """
        INSERT INTO identities(
            send_as_id, username, label, daohao, realm, spiritual_root_type, spiritual_root_attrs, replica_professions, replica_gold_dps_enabled, pet_name, pet_warm_name, pet_trial_name, sect_name, sect_updated_at, jiyin_choice, nanlong_choice, stargazer_star_choice, tianti_rank_choice, stargazer_total_slots,
            checkin_window_start_hour_utc, checkin_window_end_hour_utc,
            tower_window_start_hour_utc, tower_window_end_hour_utc,
            enabled, xiuwei_current, xiuwei_max, battle_power_text, battle_power_value, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(send_as_id) DO UPDATE SET
            username=excluded.username,
            label=excluded.label,
            daohao=excluded.daohao,
            realm=excluded.realm,
            spiritual_root_type=excluded.spiritual_root_type,
            spiritual_root_attrs=excluded.spiritual_root_attrs,
            replica_professions=excluded.replica_professions,
            replica_gold_dps_enabled=excluded.replica_gold_dps_enabled,
            pet_name=excluded.pet_name,
            pet_warm_name=excluded.pet_warm_name,
            pet_trial_name=excluded.pet_trial_name,
            sect_name=excluded.sect_name,
            sect_updated_at=excluded.sect_updated_at,
            jiyin_choice=excluded.jiyin_choice,
            nanlong_choice=excluded.nanlong_choice,
            stargazer_star_choice=excluded.stargazer_star_choice,
            tianti_rank_choice=excluded.tianti_rank_choice,
            stargazer_total_slots=excluded.stargazer_total_slots,
            checkin_window_start_hour_utc=excluded.checkin_window_start_hour_utc,
            checkin_window_end_hour_utc=excluded.checkin_window_end_hour_utc,
            tower_window_start_hour_utc=excluded.tower_window_start_hour_utc,
            tower_window_end_hour_utc=excluded.tower_window_end_hour_utc,
            enabled=excluded.enabled,
            xiuwei_current=excluded.xiuwei_current,
            xiuwei_max=excluded.xiuwei_max,
            battle_power_text=excluded.battle_power_text,
            battle_power_value=excluded.battle_power_value,
            updated_at=excluded.updated_at
        """,
        (
            int(send_as_id),
            profile.get("username", "") or "",
            profile.get("label", "") or "",
            profile.get("daohao", "") or "",
            profile.get("realm", "") or "",
            profile.get("spiritual_root_type", "") or "",
            profile.get("spiritual_root_attrs", "") or "",
            profile.get("replica_professions", "") or "",
            1 if profile.get("replica_gold_dps_enabled", False) else 0,
            profile.get("pet_name", "") or "",
            profile.get("pet_warm_name", "") or "",
            profile.get("pet_trial_name", "") or "",
            profile.get("sect_name", "") or "",
            float(profile.get("sect_updated_at", 0) or 0),
            profile.get("jiyin_choice", "") or "",
            profile.get("nanlong_choice", "reject") or "reject",
            profile.get("stargazer_star_choice", "赤血星") or "赤血星",
            profile.get("tianti_rank_choice", "普通") or "普通",
            int(profile.get("stargazer_total_slots", 0) or 0),
            int(profile.get("checkin_window_start_hour_utc", 2) or 2),
            int(profile.get("checkin_window_end_hour_utc", 3) or 3),
            int(profile.get("tower_window_start_hour_utc", 1) or 1),
            int(profile.get("tower_window_end_hour_utc", 2) or 2),
            1 if profile.get("enabled", True) else 0,
            int(profile.get("xiuwei_current", 0) or 0),
            int(profile.get("xiuwei_max", 0) or 0),
            profile.get("battle_power_text", "") or "",
            int(profile.get("battle_power_value", 0) or 0),
            now_ts,
            now_ts,
        ),
    )

    module_values = [_serialize_db_value(col, identity_state.get(col)) for col in IDENTITY_MODULE_COLUMNS]
    conn.execute(
        f"""
        INSERT INTO identity_module_state(send_as_id, {', '.join(IDENTITY_MODULE_COLUMNS)})
        VALUES ({', '.join(['?'] * (len(IDENTITY_MODULE_COLUMNS) + 1))})
        ON CONFLICT(send_as_id) DO UPDATE SET
            {', '.join(f'{col}=excluded.{col}' for col in IDENTITY_MODULE_COLUMNS)}
        """,
        [int(send_as_id), *module_values],
    )

    timer_values = [_serialize_db_value(col, identity_state.get(col)) for col in IDENTITY_TIMER_COLUMNS]
    conn.execute(
        f"""
        INSERT INTO identity_timers(send_as_id, {', '.join(IDENTITY_TIMER_COLUMNS)})
        VALUES ({', '.join(['?'] * (len(IDENTITY_TIMER_COLUMNS) + 1))})
        ON CONFLICT(send_as_id) DO UPDATE SET
            {', '.join(f'{col}=excluded.{col}' for col in IDENTITY_TIMER_COLUMNS)}
        """,
        [int(send_as_id), *timer_values],
    )

    runtime_values = [_serialize_db_value(col, identity_state.get(col)) for col in IDENTITY_RUNTIME_COLUMNS]
    conn.execute(
        f"""
        INSERT INTO identity_runtime_state(send_as_id, {', '.join(IDENTITY_RUNTIME_COLUMNS)})
        VALUES ({', '.join(['?'] * (len(IDENTITY_RUNTIME_COLUMNS) + 1))})
        ON CONFLICT(send_as_id) DO UPDATE SET
            {', '.join(f'{col}=excluded.{col}' for col in IDENTITY_RUNTIME_COLUMNS)}
        """,
        [int(send_as_id), *runtime_values],
    )

    conn.execute("DELETE FROM pending_tasks WHERE send_as_id = ?", (int(send_as_id),))
    for msg_id, item in identity_state.get("pending_tasks", {}).items():
        conn.execute(
            "INSERT OR REPLACE INTO pending_tasks(msg_id, send_as_id, cmd, sent_at, retry, timeout, reply_to_msg_id, max_retry, priority, source_module, op_id, chain_id, delete_policy) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(msg_id),
                int(send_as_id),
                get_pending_command(item),
                float(item.get("sent_at", 0) or 0),
                int(item.get("retry", 0) or 0),
                float(item.get("timeout", 0) or 0),
                int(item.get("reply_to_msg_id", 0) or 0),
                int(item.get("max_retry", RETRY_LIMIT) if item.get("max_retry", RETRY_LIMIT) is not None else RETRY_LIMIT),
                str(item.get("priority", "") or ""),
                str(item.get("source_module", "") or ""),
                str(item.get("op_id", "") or ""),
                str(item.get("chain_id", "") or ""),
                str(item.get("delete_policy", "") or ""),
            ),
        )

    conn.execute("DELETE FROM message_index WHERE send_as_id = ?", (int(send_as_id),))
    for msg_id, sent_at in identity_state.get("my_msg_ids", {}).items():
        conn.execute(
            "INSERT OR REPLACE INTO message_index(msg_id, send_as_id, sent_at, kind) VALUES (?, ?, ?, ?)",
            (int(msg_id), int(send_as_id), float(sent_at or 0), "command"),
        )


def _load_identity_columns_from_row(identity_state, row, columns):
    row_keys = set(row.keys())
    for col in columns:
        if col in row_keys:
            identity_state[col] = _deserialize_db_value(col, row[col])


def _load_identity_from_db(send_as_id):
    conn = get_db_conn()
    identity_state = new_identity_state()

    row = conn.execute(
        "SELECT username, label, daohao, realm, spiritual_root_type, spiritual_root_attrs, replica_professions, replica_gold_dps_enabled, pet_name, pet_warm_name, pet_trial_name, sect_name, sect_updated_at, jiyin_choice, nanlong_choice, stargazer_star_choice, tianti_rank_choice, stargazer_total_slots, checkin_window_start_hour_utc, checkin_window_end_hour_utc, tower_window_start_hour_utc, tower_window_end_hour_utc, enabled, xiuwei_current, xiuwei_max, battle_power_text, battle_power_value FROM identities WHERE send_as_id = ?",
        (int(send_as_id),),
    ).fetchone()
    if row:
        set_send_as_profile(
            send_as_id,
            row["username"],
            row["label"],
            daohao=row["daohao"],
            realm=row["realm"],
            spiritual_root_type=row["spiritual_root_type"],
            spiritual_root_attrs=row["spiritual_root_attrs"],
            replica_professions=row["replica_professions"],
            replica_gold_dps_enabled=bool(row["replica_gold_dps_enabled"]),
            pet_name=row["pet_name"],
            pet_warm_name=row["pet_warm_name"],
            pet_trial_name=row["pet_trial_name"],
            sect_name=row["sect_name"],
            sect_updated_at=row["sect_updated_at"],
            jiyin_choice=row["jiyin_choice"],
            nanlong_choice=row["nanlong_choice"],
            stargazer_star_choice=row["stargazer_star_choice"],
            tianti_rank_choice=row["tianti_rank_choice"],
            stargazer_total_slots=int(row["stargazer_total_slots"] or 0),
            checkin_window_start_hour_utc=row["checkin_window_start_hour_utc"],
            checkin_window_end_hour_utc=row["checkin_window_end_hour_utc"],
            tower_window_start_hour_utc=row["tower_window_start_hour_utc"],
            tower_window_end_hour_utc=row["tower_window_end_hour_utc"],
            enabled=bool(row["enabled"]),
            xiuwei_current=int(row["xiuwei_current"] or 0),
            xiuwei_max=int(row["xiuwei_max"] or 0),
            battle_power_text=row["battle_power_text"],
            battle_power_value=int(row["battle_power_value"] or 0),
        )

    row = conn.execute("SELECT * FROM identity_module_state WHERE send_as_id = ?", (int(send_as_id),)).fetchone()
    if row:
        _load_identity_columns_from_row(identity_state, row, IDENTITY_MODULE_COLUMNS)

    row = conn.execute("SELECT * FROM identity_timers WHERE send_as_id = ?", (int(send_as_id),)).fetchone()
    if row:
        _load_identity_columns_from_row(identity_state, row, IDENTITY_TIMER_COLUMNS)

    row = conn.execute("SELECT * FROM identity_runtime_state WHERE send_as_id = ?", (int(send_as_id),)).fetchone()
    if row:
        _load_identity_columns_from_row(identity_state, row, IDENTITY_RUNTIME_COLUMNS)

    pending_rows = conn.execute("SELECT * FROM pending_tasks WHERE send_as_id = ?", (int(send_as_id),)).fetchall()
    identity_state["pending_tasks"] = {
        int(row["msg_id"]): {
            "cmd": row["cmd"],
            "sent_at": row["sent_at"],
            "retry": row["retry"],
            "timeout": row["timeout"],
            "reply_to_msg_id": row["reply_to_msg_id"],
            "max_retry": row["max_retry"] if "max_retry" in row.keys() else RETRY_LIMIT,
            "priority": row["priority"] if "priority" in row.keys() else "",
            "source_module": row["source_module"] if "source_module" in row.keys() else "",
            "op_id": row["op_id"] if "op_id" in row.keys() else "",
            "chain_id": row["chain_id"] if "chain_id" in row.keys() else "",
            "delete_policy": row["delete_policy"] if "delete_policy" in row.keys() else "",
        }
        for row in pending_rows
    }

    index_rows = conn.execute("SELECT msg_id, sent_at FROM message_index WHERE send_as_id = ?", (int(send_as_id),)).fetchall()
    identity_state["my_msg_ids"] = {int(row["msg_id"]): row["sent_at"] for row in index_rows}

    _meta_state["identity_states"][int(send_as_id)] = identity_state
    return identity_state


def _encode_meta_json(value):
    return json.dumps(value, ensure_ascii=False)


def _decode_meta_json(value, fallback):
    try:
        return json.loads(value or _encode_meta_json(fallback))
    except Exception:
        return fallback


def _decode_meta_int(value, fallback=0):
    try:
        return int(value or fallback)
    except (TypeError, ValueError):
        return fallback


def _decode_meta_float(value, fallback=0.0):
    try:
        return float(value or fallback)
    except (TypeError, ValueError):
        return fallback


def _decode_meta_bool_flag(value, fallback=True):
    if value is None:
        return bool(fallback)
    return str(value).strip() not in {"0", "false", "False", "off", "OFF"}


def _set_meta_value(key, value):
    _meta_state[key] = value
    return value


def _get_delayed_actions_state():
    bucket = {}
    export_delayed_actions_to_state(bucket)
    snapshot = bucket.get(DELAYED_ACTIONS_STATE_KEY, {})
    _meta_state[DELAYED_ACTIONS_STATE_KEY] = snapshot
    return snapshot


def _restore_delayed_actions_state(value):
    bucket = {DELAYED_ACTIONS_STATE_KEY: _decode_meta_json(value, {})}
    restored = restore_delayed_actions_from_state(bucket)
    _meta_state[DELAYED_ACTIONS_STATE_KEY] = restored
    return restored


_META_STATE_CODEC = {
    "game_group_id": (
        get_game_group_id,
        lambda value: str(value),
        lambda value: set_game_group_id(_decode_meta_int(value, 0)),
    ),
    "game_bot_ids": (
        get_game_bot_ids,
        _encode_meta_json,
        lambda value: set_game_bot_ids(_decode_meta_json(value, [])),
    ),
    "game_topic_id": (
        get_game_topic_id,
        lambda value: str(value),
        lambda value: set_game_topic_id(_decode_meta_int(value, 0)),
    ),
    "forum_topics": (
        get_forum_topics,
        _encode_meta_json,
        lambda value: _decode_meta_json(value, []),
    ),
    "forum_topics_updated_at": (
        get_forum_topics_updated_at,
        lambda value: str(value),
        lambda value: _decode_meta_float(value, 0),
    ),
    "auto_delete_sent_messages": (
        is_auto_delete_sent_messages_enabled,
        lambda value: "1" if value else "0",
        lambda value: set_auto_delete_sent_messages(_decode_meta_bool_flag(value, True)),
    ),
    "global_enabled": (
        get_global_enabled,
        lambda value: "1" if value else "0",
        lambda value: set_global_enabled(_decode_meta_bool_flag(value, True)),
    ),
    "tiandao_judgement_enabled": (
        get_tiandao_judgement_enabled,
        lambda value: "1" if value else "0",
        lambda value: set_tiandao_judgement_enabled(_decode_meta_bool_flag(value, False)),
    ),
    "tiandao_judgement_pending": (
        lambda: _meta_state.get("tiandao_judgement_pending") if isinstance(_meta_state.get("tiandao_judgement_pending"), dict) else {},
        _encode_meta_json,
        lambda value: _set_meta_value("tiandao_judgement_pending", _decode_meta_json(value, {})),
    ),
    "tianji_quiz_pending": (
        lambda: _meta_state.get("tianji_quiz_pending") if isinstance(_meta_state.get("tianji_quiz_pending"), dict) else {},
        _encode_meta_json,
        lambda value: _set_meta_value("tianji_quiz_pending", _decode_meta_json(value, {})),
    ),
    "divination_pending_exchanges": (
        get_divination_pending_exchanges,
        _encode_meta_json,
        lambda value: set_divination_pending_exchanges(_decode_meta_json(value, {})),
    ),
    "divination_run_state": (
        get_divination_run_state,
        _encode_meta_json,
        lambda value: set_divination_run_state(_decode_meta_json(value, {})),
    ),
    "world_boss_run_state": (
        get_world_boss_run_state,
        _encode_meta_json,
        lambda value: set_world_boss_run_state(_decode_meta_json(value, {})),
    ),
    "guanxing_monitor_enabled": (
        get_guanxing_monitor_enabled,
        lambda value: "1" if value else "0",
        lambda value: set_guanxing_monitor_enabled(_decode_meta_bool_flag(value, False)),
    ),
    "guanxing_monitor_targets": (
        get_guanxing_monitor_targets,
        _encode_meta_json,
        lambda value: set_guanxing_monitor_targets(_decode_meta_json(value, ["地磁暴动", "星辰异象"])),
    ),
    "guanxing_shift_target": (
        get_guanxing_shift_target,
        lambda value: str(value or ""),
        lambda value: set_guanxing_shift_target(str(value or "")),
    ),
    "guanxing_shift_delay_sec": (
        get_guanxing_shift_delay_sec,
        lambda value: str(int(value)),
        set_guanxing_shift_delay_sec,
    ),
    "next_guanxing_monitor_notify_time": (
        lambda: float(_meta_state.get("next_guanxing_monitor_notify_time", 0) or 0),
        lambda value: str(value),
        lambda value: _set_meta_value("next_guanxing_monitor_notify_time", _decode_meta_float(value, 0)),
    ),
    "guanxing_monitor_slot_key": (
        lambda: str(_meta_state.get("guanxing_monitor_slot_key") or ""),
        lambda value: str(value or ""),
        lambda value: _set_meta_value("guanxing_monitor_slot_key", str(value or "")),
    ),
    "guanxing_monitor_slot_start_at": (
        lambda: float(_meta_state.get("guanxing_monitor_slot_start_at", 0) or 0),
        lambda value: str(value),
        lambda value: _set_meta_value("guanxing_monitor_slot_start_at", _decode_meta_float(value, 0)),
    ),
    "guanxing_monitor_slot_end_at": (
        lambda: float(_meta_state.get("guanxing_monitor_slot_end_at", 0) or 0),
        lambda value: str(value),
        lambda value: _set_meta_value("guanxing_monitor_slot_end_at", _decode_meta_float(value, 0)),
    ),
    "guanxing_monitor_seen_panel": (
        lambda: bool(_meta_state.get("guanxing_monitor_seen_panel", False)),
        lambda value: "1" if value else "0",
        lambda value: _set_meta_value("guanxing_monitor_seen_panel", _decode_meta_bool_flag(value, False)),
    ),
    "guanxing_monitor_matched_keyword": (
        lambda: str(_meta_state.get("guanxing_monitor_matched_keyword") or ""),
        lambda value: str(value or ""),
        lambda value: _set_meta_value("guanxing_monitor_matched_keyword", str(value or "")),
    ),
    "guanxing_monitor_matched_value": (
        lambda: str(_meta_state.get("guanxing_monitor_matched_value") or ""),
        lambda value: str(value or ""),
        lambda value: _set_meta_value("guanxing_monitor_matched_value", str(value or "")),
    ),
    "guanxing_monitor_last_evolution_value": (
        lambda: str(_meta_state.get("guanxing_monitor_last_evolution_value") or ""),
        lambda value: str(value or ""),
        lambda value: _set_meta_value("guanxing_monitor_last_evolution_value", str(value or "")),
    ),
    "guanxing_monitor_last_seen_at": (
        lambda: float(_meta_state.get("guanxing_monitor_last_seen_at", 0) or 0),
        lambda value: str(value),
        lambda value: _set_meta_value("guanxing_monitor_last_seen_at", _decode_meta_float(value, 0)),
    ),
    "guanxing_monitor_last_notified_slot_key": (
        lambda: str(_meta_state.get("guanxing_monitor_last_notified_slot_key") or ""),
        lambda value: str(value or ""),
        lambda value: _set_meta_value("guanxing_monitor_last_notified_slot_key", str(value or "")),
    ),
    "guanxing_round_state": (
        get_guanxing_round_state,
        _encode_meta_json,
        lambda value: set_guanxing_round_state(_decode_meta_json(value, {})),
    ),
    "formation_run_state": (
        get_formation_run_state,
        _encode_meta_json,
        lambda value: set_formation_run_state(_decode_meta_json(value, {})),
    ),
    "replica_group_id": (
        get_replica_group_id,
        lambda value: str(value),
        lambda value: set_replica_group_id(_decode_meta_int(value, 0)),
    ),
    "replica_group_ids": (
        get_replica_group_ids,
        _encode_meta_json,
        lambda value: set_replica_group_ids(_decode_meta_json(value, [])),
    ),
    "replica_listener_account_id": (
        get_replica_listener_account_id,
        lambda value: str(value),
        lambda value: set_replica_listener_account_id(_decode_meta_int(value, 0)),
    ),
    "replica_listener_account_map": (
        get_replica_listener_account_map,
        _encode_meta_json,
        lambda value: set_replica_listener_account_map(_decode_meta_json(value, {})),
    ),
    "replica_dispatch_group_ids": (
        get_replica_dispatch_group_ids,
        _encode_meta_json,
        lambda value: set_replica_dispatch_group_ids(_decode_meta_json(value, [])),
    ),
    "replica_dispatch_listener_account_map": (
        get_replica_dispatch_listener_account_map,
        _encode_meta_json,
        lambda value: set_replica_dispatch_listener_account_map(_decode_meta_json(value, {})),
    ),
    "replica_participant_identity_ids": (
        get_replica_participant_identity_ids,
        _encode_meta_json,
        lambda value: set_replica_participant_identity_ids(_decode_meta_json(value, [])),
    ),
    "replica_dispatch_participant_identity_ids": (
        get_replica_dispatch_participant_identity_ids,
        _encode_meta_json,
        lambda value: set_replica_dispatch_participant_identity_ids(_decode_meta_json(value, [])),
    ),
    "replica_run_state": (
        get_replica_run_state,
        _encode_meta_json,
        lambda value: set_replica_run_state(_decode_meta_json(value, {})),
    ),
    "replica_virtual_hall_match_enabled_map": (
        get_replica_virtual_hall_match_enabled_map,
        _encode_meta_json,
        lambda value: set_replica_virtual_hall_match_enabled_map(_decode_meta_json(value, {})),
    ),
    "replica_query_aggregator_config": (
        get_replica_query_aggregator_config,
        _encode_meta_json,
        lambda value: set_replica_query_aggregator_config(_decode_meta_json(value, {})),
    ),
    "storage_bag_api_config": (
        get_storage_bag_api_config,
        _encode_meta_json,
        lambda value: set_storage_bag_api_config(_decode_meta_json(value, {})),
    ),
    "storage_bag_records": (
        get_storage_bag_records,
        _encode_meta_json,
        lambda value: set_storage_bag_records(_decode_meta_json(value, {})),
    ),
    "storage_bag_item_rules": (
        get_storage_bag_item_rules,
        _encode_meta_json,
        lambda value: set_storage_bag_item_rules(_decode_meta_json(value, {})),
    ),
    "tianjige_dao_path_records": (
        get_tianjige_dao_path_records,
        _encode_meta_json,
        lambda value: set_tianjige_dao_path_records(_decode_meta_json(value, {})),
    ),
    "dungeon_join_run_state": (
        get_dungeon_join_run_state,
        _encode_meta_json,
        lambda value: set_dungeon_join_run_state(_decode_meta_json(value, {})),
    ),
    "dungeon_quiet_until": (
        lambda: float(_meta_state.get("dungeon_quiet_until", 0) or 0),
        lambda value: str(value),
        lambda value: _set_meta_value("dungeon_quiet_until", _decode_meta_float(value, 0)),
    ),
    "dungeon_quiet_reason": (
        lambda: str(_meta_state.get("dungeon_quiet_reason") or ""),
        lambda value: str(value or ""),
        lambda value: _set_meta_value("dungeon_quiet_reason", str(value or "")),
    ),
    "dungeon_quiet_last_log_at": (
        lambda: float(_meta_state.get("dungeon_quiet_last_log_at", 0) or 0),
        lambda value: str(value),
        lambda value: _set_meta_value("dungeon_quiet_last_log_at", _decode_meta_float(value, 0)),
    ),
    "quiz_learning_watchers": (
        get_quiz_learning_watchers,
        _encode_meta_json,
        lambda value: set_quiz_learning_watchers(_decode_meta_json(value, {})),
    ),
    "quiz_ai_config": (
        get_quiz_ai_config,
        _encode_meta_json,
        lambda value: set_quiz_ai_config(_decode_meta_json(value, {})),
    ),
    "accounts": (
        get_accounts,
        _encode_meta_json,
        lambda value: set_accounts(_decode_meta_json(value, {})),
    ),
    "identity_account_map": (
        get_identity_account_map,
        _encode_meta_json,
        lambda value: set_identity_account_map(_decode_meta_json(value, {})),
    ),
    "identity_membership_initialized": (
        lambda: bool(_meta_state.get("identity_membership_initialized", False)),
        lambda value: "1" if value else "0",
        lambda value: _set_meta_value("identity_membership_initialized", _decode_meta_bool_flag(value, False)),
    ),
    "delayed_actions_state": (
        _get_delayed_actions_state,
        _encode_meta_json,
        _restore_delayed_actions_state,
    ),
}


def save_quiz_learning_watchers_state():
    try:
        init_db()
        conn = get_db_conn()
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (
                "quiz_learning_watchers",
                _META_STATE_CODEC["quiz_learning_watchers"][1](get_quiz_learning_watchers()),
            ),
        )
        conn.commit()
    except Exception:
        traceback.print_exc()


def save_quiz_ai_config_state():
    try:
        init_db()
        conn = get_db_conn()
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (
                "quiz_ai_config",
                _META_STATE_CODEC["quiz_ai_config"][1](get_quiz_ai_config()),
            ),
        )
        conn.commit()
    except Exception:
        traceback.print_exc()


def _sync_external_safety_pause_before_save(conn):
    if not get_global_enabled():
        return
    if not os.path.exists(_safety_watchdog_fused_file()):
        return
    row = conn.execute("SELECT value FROM meta WHERE key = ?", ("global_enabled",)).fetchone()
    if not row:
        return
    if str(row["value"] or "").strip() in {"0", "false", "False", ""}:
        set_global_enabled(False)


def _save_meta_state(conn):
    for key, (getter, encoder, _) in _META_STATE_CODEC.items():
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (key, encoder(getter())),
        )


def _identity_collapse_guard_enabled():
    if os.environ.get("XIUXIAN_ALLOW_IDENTITY_COLLAPSE") == "1":
        return False
    if os.environ.get("XIUXIAN_TESTING") == "1" and os.environ.get("XIUXIAN_ENFORCE_IDENTITY_GUARD") != "1":
        return False
    return True


def _looks_like_demo_identity_set(identity_ids, profiles):
    current_ids = {int(send_as_id) for send_as_id in identity_ids}
    if current_ids == {991201}:
        return True
    if len(current_ids) > 3:
        return False
    usernames = {
        str((profiles.get(send_as_id) or profiles.get(str(send_as_id)) or {}).get("username") or "").strip().lower()
        for send_as_id in current_ids
    }
    usernames.discard("")
    return bool(usernames & {"leader", "@leader", "test", "@test"})


def _should_block_identity_collapse(existing_ids, current_ids):
    if not _identity_collapse_guard_enabled():
        return False
    existing_ids = {int(send_as_id) for send_as_id in existing_ids}
    current_ids = {int(send_as_id) for send_as_id in current_ids}
    if len(existing_ids) < 10:
        return False
    if len(current_ids) >= max(4, len(existing_ids) // 2):
        return False
    deleted_ids = existing_ids - current_ids
    if len(deleted_ids) < 3:
        return False
    profiles = _meta_state.get("send_as_profiles") if isinstance(_meta_state.get("send_as_profiles"), dict) else {}
    if _looks_like_demo_identity_set(current_ids, profiles):
        return True
    return len(current_ids) <= 2 and len(current_ids & existing_ids) <= 1


def _read_identity_roster_from_conn(conn):
    try:
        rows = conn.execute("SELECT send_as_id, username FROM identities ORDER BY send_as_id").fetchall()
    except Exception:
        return []
    roster = []
    for row in rows:
        try:
            send_as_id = int(row["send_as_id"])
        except Exception:
            continue
        try:
            username = str(row["username"] or "")
        except Exception:
            username = ""
        roster.append((send_as_id, username))
    return roster


def _read_identity_roster_from_db_file(db_file):
    if not db_file or not os.path.exists(db_file):
        return []
    conn = None
    try:
        conn = sqlite3.connect(db_file)
        conn.row_factory = sqlite3.Row
        return _read_identity_roster_from_conn(conn)
    except Exception:
        return []
    finally:
        if conn is not None:
            conn.close()


def _roster_looks_like_live(roster):
    return len(roster or []) >= 10


def _roster_looks_suspicious(roster):
    if not roster:
        return False
    ids = [int(item[0]) for item in roster]
    profiles = {int(send_as_id): {"username": username} for send_as_id, username in roster}
    if _looks_like_demo_identity_set(ids, profiles):
        return True
    return len(roster) <= 2


def _write_live_guard_backup(conn):
    if not _identity_collapse_guard_enabled():
        return
    if os.environ.get("XIUXIAN_DISABLE_LIVE_DB_BACKUP") == "1":
        return
    roster = _read_identity_roster_from_conn(conn)
    if not _roster_looks_like_live(roster):
        return
    try:
        os.makedirs(LIVE_GUARD_DIR, exist_ok=True)
        backup_conn = sqlite3.connect(LIVE_GUARD_DB_FILE)
        try:
            conn.backup(backup_conn)
        finally:
            backup_conn.close()
        manifest = {
            "saved_at": time.time(),
            "identity_count": len(roster),
            "identity_ids": [send_as_id for send_as_id, _username in roster],
        }
        tmp_manifest = LIVE_GUARD_MANIFEST_FILE + ".tmp"
        with open(tmp_manifest, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_manifest, LIVE_GUARD_MANIFEST_FILE)
    except Exception:
        traceback.print_exc()


def _maybe_restore_live_guard_backup():
    if not _identity_collapse_guard_enabled():
        return False
    if os.environ.get("XIUXIAN_DISABLE_LIVE_DB_RESTORE") == "1":
        return False
    if os.environ.get("XIUXIAN_ALLOW_IDENTITY_COLLAPSE") == "1":
        return False
    current_roster = _read_identity_roster_from_db_file(DB_FILE)
    if not _roster_looks_suspicious(current_roster):
        return False
    backup_roster = _read_identity_roster_from_db_file(LIVE_GUARD_DB_FILE)
    if not _roster_looks_like_live(backup_roster):
        return False
    try:
        if _db_conn is not None:
            _db_conn.close()
        backup_name = f"{DB_FILE}.suspicious-{int(time.time())}"
        if os.path.exists(DB_FILE):
            shutil.copy2(DB_FILE, backup_name)
        shutil.copy2(LIVE_GUARD_DB_FILE, DB_FILE)
        print(
            "Restored live state DB from guard backup after suspicious roster: "
            f"current={len(current_roster)} backup={len(backup_roster)} saved_bad={backup_name}"
        )
        return True
    except Exception:
        traceback.print_exc()
        return False



def delete_identity_from_db(send_as_id):
    init_db()
    conn = get_db_conn()
    send_as_id = int(send_as_id)
    conn.execute("DELETE FROM pending_tasks WHERE send_as_id = ?", (send_as_id,))
    conn.execute("DELETE FROM message_index WHERE send_as_id = ?", (send_as_id,))
    conn.execute("DELETE FROM identity_runtime_state WHERE send_as_id = ?", (send_as_id,))
    conn.execute("DELETE FROM identity_timers WHERE send_as_id = ?", (send_as_id,))
    conn.execute("DELETE FROM identity_module_state WHERE send_as_id = ?", (send_as_id,))
    conn.execute("DELETE FROM identities WHERE send_as_id = ?", (send_as_id,))



def save_state():
    global _state_dirty, _last_flush_time
    try:
        init_db()
        conn = get_db_conn()
        _ensure_schema_columns(conn)
        _sync_external_safety_pause_before_save(conn)
        _save_meta_state(conn)
        existing_ids = {
            int(row["send_as_id"])
            for row in conn.execute("SELECT send_as_id FROM identities").fetchall()
        }
        current_ids = {int(send_as_id) for send_as_id in get_identity_ids()}
        if _should_block_identity_collapse(existing_ids, current_ids):
            print(
                "Refusing to save suspicious identity collapse: "
                f"existing={len(existing_ids)} current={len(current_ids)} "
                f"deleted={sorted(existing_ids - current_ids)}"
            )
            conn.rollback()
            return False
        for send_as_id in sorted(existing_ids - current_ids):
            delete_identity_from_db(send_as_id)
        for send_as_id in get_identity_ids():
            upsert_identity_to_db(send_as_id)
        conn.commit()
        _write_live_guard_backup(conn)
        _state_dirty = False
        _last_flush_time = time.time()
        _mark_persistence_save_ok()
        return True
    except Exception as exc:
        _mark_persistence_save_failed(exc)
        traceback.print_exc()
        return False


def mark_dirty():
    global _state_dirty
    _state_dirty = True


def flush_if_dirty(now=None):
    if not _state_dirty:
        return True
    if now is None:
        now = time.time()
    if now - _last_flush_time >= FLUSH_INTERVAL_SEC:
        return save_state()
    return True


def has_persisted_identity_rows():
    if not os.path.exists(DB_FILE):
        return False
    try:
        with sqlite3.connect(DB_FILE) as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='identities'"
            ).fetchone()
            if not row:
                return False
            count = conn.execute("SELECT COUNT(*) FROM identities").fetchone()[0]
            return int(count or 0) > 0
    except Exception:
        return True


def load_state():
    try:
        restored_db = _maybe_restore_live_guard_backup()
        if restored_db:
            global _db_conn, _db_initialized
            _db_conn = None
            _db_initialized = False
        init_db()
        conn = get_db_conn()
        _ensure_schema_columns(conn)
        conn.commit()

        meta_rows = conn.execute("SELECT key, value FROM meta").fetchall()
        meta_map = {str(row["key"] or ""): row["value"] for row in meta_rows}
        forum_topics = []
        forum_topics_updated_at = 0
        membership_initialized = False
        replica_participant_identity_ids = []
        replica_dispatch_participant_identity_ids = []
        replica_group_ids = []
        replica_listener_account_map = {}
        replica_virtual_hall_match_enabled_map = {}
        for key, (_, _, decoder) in _META_STATE_CODEC.items():
            if key == "replica_group_ids":
                replica_group_ids = _decode_meta_json(meta_map.get(key), [])
                continue
            if key == "replica_listener_account_map":
                replica_listener_account_map = _decode_meta_json(meta_map.get(key), {})
                continue
            if key == "replica_participant_identity_ids":
                replica_participant_identity_ids = _decode_meta_json(meta_map.get(key), [])
                continue
            if key == "replica_dispatch_participant_identity_ids":
                replica_dispatch_participant_identity_ids = _decode_meta_json(meta_map.get(key), [])
                continue
            if key == "replica_virtual_hall_match_enabled_map":
                replica_virtual_hall_match_enabled_map = _decode_meta_json(meta_map.get(key), {})
                continue
            decoded = decoder(meta_map.get(key))
            if key == "forum_topics":
                forum_topics = decoded
            elif key == "forum_topics_updated_at":
                forum_topics_updated_at = decoded
            elif key == "identity_membership_initialized":
                membership_initialized = bool(decoded)
        if not replica_group_ids:
            legacy_group_id = _decode_meta_int(meta_map.get("replica_group_id"), 0)
            replica_group_ids = [legacy_group_id] if legacy_group_id else []
        if not replica_listener_account_map:
            legacy_group_id = _decode_meta_int(meta_map.get("replica_group_id"), 0)
            legacy_account_id = _decode_meta_int(meta_map.get("replica_listener_account_id"), 0)
            if legacy_group_id and legacy_account_id:
                replica_listener_account_map = {str(legacy_group_id): legacy_account_id}
        set_replica_group_ids(replica_group_ids)
        set_replica_listener_account_map(replica_listener_account_map)
        set_replica_virtual_hall_match_enabled_map(replica_virtual_hall_match_enabled_map)
        set_forum_topics(forum_topics, updated_at=forum_topics_updated_at)
        rows = conn.execute(
            "SELECT send_as_id, username, label, daohao, realm, pet_name, pet_warm_name, pet_trial_name, sect_name, sect_updated_at, stargazer_star_choice, tianti_rank_choice, stargazer_total_slots, checkin_window_start_hour_utc, checkin_window_end_hour_utc, tower_window_start_hour_utc, tower_window_end_hour_utc, enabled, xiuwei_current, xiuwei_max FROM identities ORDER BY send_as_id"
        ).fetchall()

        _meta_state["identity_ids"] = []
        _meta_state["identity_states"] = {}
        for row in rows:
            send_as_id = int(row["send_as_id"])
            _meta_state["identity_ids"].append(send_as_id)
            _load_identity_from_db(send_as_id)
        set_replica_participant_identity_ids(replica_participant_identity_ids)
        set_replica_dispatch_participant_identity_ids(replica_dispatch_participant_identity_ids)

        if not membership_initialized:
            _save_meta_state(conn)
            for send_as_id in get_identity_ids():
                ensure_identity_registered(send_as_id)
                upsert_identity_to_db(send_as_id)
            conn.commit()

        _mark_persistence_save_ok()
        return True
    except Exception as exc:
        _mark_persistence_save_failed(exc)
        traceback.print_exc()
        return False


configure_timing(save_state)


__all__ = [
    "flush_if_dirty",
    "get_db_conn",
    "get_persistence_write_failure",
    "has_persisted_identity_rows",
    "has_persistence_write_failure",
    "init_db",
    "load_state",
    "delete_identity_from_db",
    "mark_dirty",
    "save_quiz_ai_config_state",
    "save_quiz_learning_watchers_state",
    "save_state",
    "upsert_identity_to_db",
]
