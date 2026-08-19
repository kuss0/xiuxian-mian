import json
import os
import shutil
import sqlite3
import time
import traceback

from . import persistence_shadow
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
    get_game_group_route_config,
    get_account_group_memberships,
    get_game_bot_ids,
    get_game_listener_account_ids,
    get_game_topic_id,
    get_forum_topics,
    get_forum_topics_updated_at,
    is_auto_delete_sent_messages_enabled,
    get_global_enabled,
    get_global_pause_source,
    get_global_recovery_hold_until,
    get_global_recovery_throttle_until,
    get_channel_send_as_health,
    get_account_target_memberships,
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
    get_world_boss_rotation_state,
    get_quiz_ai_config,
    get_replica_group_id,
    get_replica_group_ids,
    get_replica_dispatch_group_ids,
    get_replica_dispatch_listener_account_map,
    get_replica_dispatch_participant_identity_ids,
    get_replica_kind_configs,
    get_replica_listener_account_id,
    get_replica_listener_account_map,
    get_replica_participant_identity_ids,
    get_replica_query_aggregator_config,
    get_replica_run_state,
    get_replica_success_cooldown_hours,
    get_replica_virtual_hall_match_enabled_map,
    get_send_as_profile,
    get_inventory_delta_records,
    get_miniapp_state_records,
    get_duel_target_cooldowns,
    get_storage_bag_api_config,
    get_storage_bag_item_rules,
    get_storage_bag_records,
    get_tianjige_dao_path_records,
    get_miniapp_auto_config,
    get_tree_miniapp_score_configs,
    new_identity_state,
    set_auto_delete_sent_messages,
    set_dungeon_join_run_state,
    set_formation_run_state,
    set_forum_topics,
    set_global_enabled,
    set_global_pause_source,
    set_global_recovery_hold_until,
    set_global_recovery_throttle_until,
    set_channel_send_as_health,
    set_account_target_memberships,
    set_tiandao_judgement_enabled,
    set_game_bot_ids,
    set_game_listener_account_ids,
    get_quiz_learning_watchers,
    set_game_group_id,
    set_game_group_route_config,
    set_account_group_memberships,
    set_game_topic_id,
    set_divination_pending_exchanges,
    set_divination_run_state,
    set_world_boss_run_state,
    set_world_boss_rotation_state,
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
    set_replica_kind_configs,
    set_replica_listener_account_id,
    set_replica_listener_account_map,
    set_replica_participant_identity_ids,
    set_replica_query_aggregator_config,
    set_replica_run_state,
    set_replica_success_cooldown_hours,
    set_replica_virtual_hall_match_enabled_map,
    set_send_as_profile,
    set_inventory_delta_records,
    set_miniapp_state_records,
    set_duel_target_cooldowns,
    set_storage_bag_api_config,
    set_storage_bag_item_rules,
    set_storage_bag_records,
    set_tianjige_dao_path_records,
    set_miniapp_auto_config,
    set_tree_miniapp_score_configs,
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
_schema_columns_ensured_version = None
_state_dirty = False
_last_flush_time = 0
_last_save_failed_at = 0.0
_last_save_error = ""
_persistence_snapshot_db_key = ""
_persisted_meta_snapshot = {}
_persisted_identity_snapshots = {}
SMALL_WORLD_PREACH_DEFAULT_NORMALIZED_KEY = "small_world_preach_default_normalized"
SQLITE_TIMEOUT_SEC = 15.0
SQLITE_BUSY_TIMEOUT_MS = int(SQLITE_TIMEOUT_SEC * 1000)
SQLITE_JOURNAL_MODE = os.environ.get("XIUXIAN_SQLITE_JOURNAL_MODE", "WAL").strip().upper()

LIVE_GUARD_DIR = os.path.abspath(os.environ.get("XIUXIAN_LIVE_GUARD_DIR") or "/root/xiuxian-main-live-guard")
LIVE_GUARD_DB_FILE = os.path.join(LIVE_GUARD_DIR, "chaogu_state.last-good.db")
LIVE_GUARD_PREVIOUS_DB_FILE = os.path.join(LIVE_GUARD_DIR, "chaogu_state.previous.db")
LIVE_GUARD_MANIFEST_FILE = os.path.join(LIVE_GUARD_DIR, "manifest.json")
try:
    _live_guard_refresh_sec = float(
        os.environ.get("XIUXIAN_LIVE_GUARD_BACKUP_INTERVAL_SEC")
        or os.environ.get("XIUXIAN_LIVE_GUARD_REFRESH_SEC")
        or 6 * 3600
    )
except (TypeError, ValueError, OverflowError):
    _live_guard_refresh_sec = 6 * 3600
LIVE_GUARD_REFRESH_SEC = max(
    300.0,
    _live_guard_refresh_sec,
)

IDENTITY_PROFILE_PERSISTED_COLUMNS = (
    "username",
    "username_aliases",
    "label",
    "daohao",
    "realm",
    "spiritual_root_type",
    "spiritual_root_attrs",
    "replica_professions",
    "replica_gold_dps_enabled",
    "pet_name",
    "pet_warm_name",
    "pet_trial_name",
    "sect_name",
    "sect_updated_at",
    "jiyin_choice",
    "nanlong_choice",
    "stargazer_star_choice",
    "tianti_rank_choice",
    "stargazer_total_slots",
    "checkin_window_start_hour_utc",
    "checkin_window_end_hour_utc",
    "tower_window_start_hour_utc",
    "tower_window_end_hour_utc",
    "enabled",
    "xiuwei_current",
    "xiuwei_max",
    "battle_power_text",
    "battle_power_value",
)
PENDING_TASK_PERSISTED_COLUMNS = (
    "msg_id",
    "send_as_id",
    "cmd",
    "sent_at",
    "retry",
    "timeout",
    "reply_to_msg_id",
    "chat_id",
    "topic_id",
    "max_retry",
    "priority",
    "source_module",
    "op_id",
    "chain_id",
    "delete_policy",
)


def _safety_watchdog_fused_file():
    return os.path.join(os.path.dirname(os.path.abspath(DB_FILE)), "safety_watchdog_fused.json")


def _open_sqlite_conn(db_file=None, *, row_factory=True, set_journal_mode=True):
    conn = sqlite3.connect(db_file or DB_FILE, timeout=SQLITE_TIMEOUT_SEC)
    conn.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    if set_journal_mode and SQLITE_JOURNAL_MODE:
        try:
            conn.execute(f"PRAGMA journal_mode={SQLITE_JOURNAL_MODE}")
        except sqlite3.OperationalError as exc:
            print(f"SQLite journal_mode={SQLITE_JOURNAL_MODE} skipped: {exc}", flush=True)
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn


def open_db_connection(*, row_factory=True, set_journal_mode=False):
    """Return a short-lived connection for narrowly scoped transactional stores."""

    return _open_sqlite_conn(
        DB_FILE,
        row_factory=row_factory,
        set_journal_mode=set_journal_mode,
    )


def get_db_conn():
    global _db_conn, _schema_columns_ensured_key, _schema_columns_ensured_version
    if _db_conn is None:
        _schema_columns_ensured_key = None
        _schema_columns_ensured_version = None
        _clear_persistence_snapshots()
        _db_conn = _open_sqlite_conn(DB_FILE)
    return _db_conn


def _schema_columns_cache_key(conn):
    return (
        os.path.abspath(DB_FILE),
        id(conn),
        tuple(IDENTITY_MODULE_COLUMNS),
        tuple(IDENTITY_TIMER_COLUMNS),
        tuple(IDENTITY_RUNTIME_COLUMNS),
    )


def _mark_schema_columns_ensured(conn):
    global _schema_columns_ensured_key, _schema_columns_ensured_version
    _schema_columns_ensured_key = _schema_columns_cache_key(conn)
    _schema_columns_ensured_version = int(conn.execute("PRAGMA schema_version").fetchone()[0] or 0)


def _schema_columns_complete(conn):
    expected = {
        "identities": {"send_as_id", *IDENTITY_PROFILE_PERSISTED_COLUMNS},
        "identity_module_state": {"send_as_id", *IDENTITY_MODULE_COLUMNS},
        "identity_timers": {"send_as_id", *IDENTITY_TIMER_COLUMNS},
        "identity_runtime_state": {"send_as_id", *IDENTITY_RUNTIME_COLUMNS},
        "pending_tasks": set(PENDING_TASK_PERSISTED_COLUMNS),
        "message_index": {"msg_id", "send_as_id", "sent_at", "kind"},
    }
    for table_name, required_columns in expected.items():
        actual_columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if not required_columns.issubset(actual_columns):
            return False
    return True


def _ensure_schema_columns_ready(conn, *, verify=False):
    if _schema_columns_ensured_key == _schema_columns_cache_key(conn):
        if not verify:
            return
        current_version = int(conn.execute("PRAGMA schema_version").fetchone()[0] or 0)
        if _schema_columns_ensured_version == current_version:
            return
        if _schema_columns_complete(conn):
            _mark_schema_columns_ensured(conn)
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


# 历次版本累积的列定义。每张表一个 (列名, 类型与约束) 序列，迁移时逐列补齐。
#
# 这里原本是 510 段手写的 `if "x" not in cols: conn.execute("ALTER TABLE ...")`，
# 同一个四行模板重复了五百遍。新增列只需在对应表末尾加一行，不必再复制模板，
# 也不会因为漏写 not-in 判断而在升级老库时炸掉。
#
# 顺序仅影响 ALTER 的执行次序，彼此独立；依赖列存在的数据回填放在 _run_schema_backfills。
_SCHEMA_COLUMNS = {
    "identity_module_state": (
        ("quiz_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ("jiyin_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("pet_trial_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("pet_warm_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("pet_formation_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("ranch_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("wild_training_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_tianji_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_heart_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_voyage_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_auto_reacquire", "INTEGER NOT NULL DEFAULT 1"),
        ("hehuan_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("tianxing_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("yinluo_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("mulan_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("wanxin_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("world_boss_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("nanlong_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("guanxing_monitor_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("guanxing_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("formation_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("last_guanxing_done_day", "TEXT NOT NULL DEFAULT ''"),
        ("tianti_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_wenxin_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ("tianti_gangfeng_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ("small_world_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_preach_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_manifest_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_harvest_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_refine_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_refresh_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_high_stock_silence_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_barrier_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ("small_world_barrier_min_stock", "INTEGER NOT NULL DEFAULT 130000"),
        ("small_world_barrier_guard_before_min", "INTEGER NOT NULL DEFAULT 30"),
        ("small_world_barrier_min_interval_hours", "REAL NOT NULL DEFAULT 18"),
        ("divination_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("dungeon_join_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("wendao_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("fishing_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("explore_rift_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("sect_teach_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("stargazer_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("second_soul_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("second_soul_auto_choice_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ("second_soul_purge_threshold", "INTEGER NOT NULL DEFAULT 60"),
        ("taiyi_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("taiyi_node_search_enabled", "INTEGER NOT NULL DEFAULT 0"),
    ),
    "identities": (
        ("username_aliases", "TEXT NOT NULL DEFAULT '[]'"),
        ("pet_name", "TEXT NOT NULL DEFAULT ''"),
        ("pet_trial_name", "TEXT NOT NULL DEFAULT ''"),
        ("pet_warm_name", "TEXT NOT NULL DEFAULT ''"),
        ("daohao", "TEXT NOT NULL DEFAULT ''"),
        ("realm", "TEXT NOT NULL DEFAULT ''"),
        ("spiritual_root_type", "TEXT NOT NULL DEFAULT ''"),
        ("spiritual_root_attrs", "TEXT NOT NULL DEFAULT ''"),
        ("replica_professions", "TEXT NOT NULL DEFAULT ''"),
        ("replica_gold_dps_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("sect_name", "TEXT NOT NULL DEFAULT ''"),
        ("sect_updated_at", "REAL NOT NULL DEFAULT 0"),
        ("jiyin_choice", "TEXT NOT NULL DEFAULT ''"),
        ("nanlong_choice", "TEXT NOT NULL DEFAULT 'reject'"),
        ("stargazer_star_choice", "TEXT NOT NULL DEFAULT '赤血星'"),
        ("stargazer_total_slots", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_rank_choice", "TEXT NOT NULL DEFAULT '普通'"),
        ("checkin_window_start_hour_utc", "INTEGER NOT NULL DEFAULT 2"),
        ("checkin_window_end_hour_utc", "INTEGER NOT NULL DEFAULT 3"),
        ("tower_window_start_hour_utc", "INTEGER NOT NULL DEFAULT 1"),
        ("tower_window_end_hour_utc", "INTEGER NOT NULL DEFAULT 2"),
        ("enabled", "INTEGER NOT NULL DEFAULT 1"),
        ("xiuwei_current", "INTEGER NOT NULL DEFAULT 0"),
        ("xiuwei_max", "INTEGER NOT NULL DEFAULT 0"),
        ("battle_power_text", "TEXT NOT NULL DEFAULT ''"),
        ("battle_power_value", "INTEGER NOT NULL DEFAULT 0"),
    ),
    "identity_timers": (
        ("next_quiz_time", "REAL NOT NULL DEFAULT 0"),
        ("next_jiyin_time", "REAL NOT NULL DEFAULT 0"),
        ("next_pet_trial_time", "REAL NOT NULL DEFAULT 0"),
        ("next_pet_warm_time", "REAL NOT NULL DEFAULT 0"),
        ("next_pet_formation_time", "REAL NOT NULL DEFAULT 0"),
        ("next_ranch_time", "REAL NOT NULL DEFAULT 0"),
        ("next_wild_training_time", "REAL NOT NULL DEFAULT 0"),
        ("next_concubine_time", "REAL NOT NULL DEFAULT 0"),
        ("next_nanlong_time", "REAL NOT NULL DEFAULT 0"),
        ("next_stargazer_panel_time", "REAL NOT NULL DEFAULT 0"),
        ("stargazer_collect_due_at", "REAL NOT NULL DEFAULT 0"),
        ("next_guanxing_monitor_notify_time", "REAL NOT NULL DEFAULT 0"),
        ("next_tianti_status_time", "REAL NOT NULL DEFAULT 0"),
        ("next_tianti_wenxin_time", "REAL NOT NULL DEFAULT 0"),
        ("next_tianti_climb_time", "REAL NOT NULL DEFAULT 0"),
        ("next_tianti_gangfeng_time", "REAL NOT NULL DEFAULT 0"),
        ("next_small_world_time", "REAL NOT NULL DEFAULT 0"),
        ("next_explore_rift_time", "REAL NOT NULL DEFAULT 0"),
        ("next_wendao_time", "REAL NOT NULL DEFAULT 0"),
        ("next_mulan_time", "REAL NOT NULL DEFAULT 0"),
        ("next_duel_time", "REAL NOT NULL DEFAULT 0"),
        ("next_fishing_time", "REAL NOT NULL DEFAULT 0"),
        ("next_formation_time", "REAL NOT NULL DEFAULT 0"),
        ("formation_cooldown_until", "REAL NOT NULL DEFAULT 0"),
        ("weak_until", "REAL NOT NULL DEFAULT 0"),
        ("next_second_soul_time", "REAL NOT NULL DEFAULT 0"),
        ("second_soul_heart_demon_deadline", "REAL NOT NULL DEFAULT 0"),
        ("next_taiyi_cycle_time", "REAL NOT NULL DEFAULT 0"),
        ("taiyi_phase_entered_at", "REAL NOT NULL DEFAULT 0"),
        ("taiyi_freeze_until", "REAL NOT NULL DEFAULT 0"),
    ),
    "identity_runtime_state": (
        ("weak_reason", "TEXT NOT NULL DEFAULT ''"),
        ("weak_source", "TEXT NOT NULL DEFAULT ''"),
        ("weak_last_block_log_at", "REAL NOT NULL DEFAULT 0"),
        ("wild_training_tianxing_prepare_retry_at", "REAL NOT NULL DEFAULT 0"),
        ("explore_rift_tianxing_prepare_retry_at", "REAL NOT NULL DEFAULT 0"),
        ("yuanying_waiting_logged", "INTEGER NOT NULL DEFAULT 0"),
        ("yuanying_protect_logged", "INTEGER NOT NULL DEFAULT 0"),
        ("deep_retreat_waiting_logged", "INTEGER NOT NULL DEFAULT 0"),
        ("deep_retreat_protect_logged", "INTEGER NOT NULL DEFAULT 0"),
        ("tree_maturing_logged", "INTEGER NOT NULL DEFAULT 0"),
        ("tree_harvest_followup_due_at", "REAL NOT NULL DEFAULT 0"),
        ("tree_harvest_inflight_until", "REAL NOT NULL DEFAULT 0"),
        ("tree_last_harvest_result_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("tree_last_harvest_reply_to_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("tree_bootstrap_check_due_at", "REAL NOT NULL DEFAULT 0"),
        ("last_tree_status_sent_at", "REAL NOT NULL DEFAULT 0"),
        ("tree_pulse_mode_seen", "INTEGER NOT NULL DEFAULT 0"),
        ("tree_pulse_last_panel_at", "REAL NOT NULL DEFAULT 0"),
        ("tree_pulse_progress", "REAL NOT NULL DEFAULT 0"),
        ("tree_pulse_main", "TEXT NOT NULL DEFAULT ''"),
        ("tree_pulse_aux", "TEXT NOT NULL DEFAULT ''"),
        ("tree_pulse_reverse", "TEXT NOT NULL DEFAULT ''"),
        ("tree_pulse_neutral", "TEXT NOT NULL DEFAULT ''"),
        ("tree_pulse_stability", "INTEGER NOT NULL DEFAULT 0"),
        ("tree_pulse_stability_max", "INTEGER NOT NULL DEFAULT 0"),
        ("tree_pulse_turbidity", "INTEGER NOT NULL DEFAULT 0"),
        ("tree_pulse_turbidity_max", "INTEGER NOT NULL DEFAULT 0"),
        ("tree_pulse_daily_used", "INTEGER NOT NULL DEFAULT 0"),
        ("tree_pulse_daily_limit", "INTEGER NOT NULL DEFAULT 0"),
        ("tree_pulse_rush_used", "INTEGER NOT NULL DEFAULT 0"),
        ("tree_pulse_rush_limit", "INTEGER NOT NULL DEFAULT 0"),
        ("tree_pulse_last_action", "TEXT NOT NULL DEFAULT ''"),
        ("tree_pulse_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("tree_pulse_blocked_until", "REAL NOT NULL DEFAULT 0"),
        ("last_tower_command_sent_at", "REAL NOT NULL DEFAULT 0"),
        ("tower_reply_due_at", "REAL NOT NULL DEFAULT 0"),
        ("tower_retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_phase", "TEXT NOT NULL DEFAULT 'idle'"),
        ("concubine_availability", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("concubine_nanlong_strategy", "TEXT NOT NULL DEFAULT 'reacquire_after_loss'"),
        ("concubine_status_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_greet_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_last_greet_day", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_greet_retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_greet_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_gift_status_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_gift_bag_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_gift_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_gift_amount", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_last_gift_day", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_gift_attempt_day", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_gift_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_dream_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_fragment_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_puzzle_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_reacquire_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_tianji_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_heart_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_heart_prompt_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_voyage_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_voyage_retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_last_panel_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_last_panel_chat_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_name", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_kind", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_location", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_affinity", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_oath", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_dream_due_at", "REAL NOT NULL DEFAULT 0"),
        ("concubine_tianji_due_at", "REAL NOT NULL DEFAULT 0"),
        ("concubine_heart_due_at", "REAL NOT NULL DEFAULT 0"),
        ("concubine_tianji_chain", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_tianji_chain_due_at", "REAL NOT NULL DEFAULT 0"),
        ("concubine_heart_round", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_heart_choice_prompt_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_heart_choice_round", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_heart_choice_sent_at", "REAL NOT NULL DEFAULT 0"),
        ("concubine_heart_choice_retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_last_recovered_reply_key", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_last_recovered_reply_at", "REAL NOT NULL DEFAULT 0"),
        ("concubine_fragment_count", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_fragment_total", "INTEGER NOT NULL DEFAULT 4"),
        ("concubine_fragment_xutian_count", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_fragment_xutian_total", "INTEGER NOT NULL DEFAULT 4"),
        ("concubine_fragment_cangkun_count", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_fragment_cangkun_total", "INTEGER NOT NULL DEFAULT 4"),
        ("concubine_fragment_confirm_key", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_fragment_confirmed_at", "REAL NOT NULL DEFAULT 0"),
        ("concubine_voyage_status", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_voyage_route", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_voyage_return_at", "REAL NOT NULL DEFAULT 0"),
        ("concubine_voyage_last_result", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_voyage_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_last_snapshot_at", "REAL NOT NULL DEFAULT 0"),
        ("concubine_reacquire_blocked_until", "REAL NOT NULL DEFAULT 0"),
        ("concubine_reacquire_attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("concubine_reacquire_command_override", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_tianji_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("concubine_heart_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("hehuan_observation", "TEXT NOT NULL DEFAULT '{}' "),
        ("tianxing_observation", "TEXT NOT NULL DEFAULT '{}' "),
        ("tianxing_auto_config", "TEXT NOT NULL DEFAULT '{}' "),
        ("tianxing_timeline_state", "TEXT NOT NULL DEFAULT '{}' "),
        ("yinluo_observation", "TEXT NOT NULL DEFAULT '{}' "),
        ("wanxin_observation", "TEXT NOT NULL DEFAULT '{}' "),
        ("world_boss_action_count", "INTEGER NOT NULL DEFAULT 0"),
        ("world_boss_action_limit", "INTEGER NOT NULL DEFAULT 5"),
        ("world_boss_attack_count", "INTEGER NOT NULL DEFAULT 0"),
        ("world_boss_pending_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("world_boss_pending_action", "TEXT NOT NULL DEFAULT ''"),
        ("world_boss_pending_since", "REAL NOT NULL DEFAULT 0"),
        ("world_boss_pending_retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("world_boss_pending_action_seq", "INTEGER NOT NULL DEFAULT 0"),
        ("world_boss_last_action", "TEXT NOT NULL DEFAULT ''"),
        ("world_boss_last_action_at", "REAL NOT NULL DEFAULT 0"),
        ("world_boss_last_reply_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("world_boss_exhausted", "INTEGER NOT NULL DEFAULT 0"),
        ("world_boss_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("pet_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("pet_trial_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("pet_warm_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("pet_formation_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("pet_formation_retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("ranch_reply_to_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("ranch_reply_due_at", "REAL NOT NULL DEFAULT 0"),
        ("ranch_retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("ranch_last_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("ranch_last_result", "TEXT NOT NULL DEFAULT ''"),
        ("ranch_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("ranch_return_pending", "INTEGER NOT NULL DEFAULT 0"),
        ("ranch_return_seen_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("ranch_return_wait_since", "REAL NOT NULL DEFAULT 0"),
        ("ranch_return_last_notified_at", "REAL NOT NULL DEFAULT 0"),
        ("wild_training_strategy", "TEXT NOT NULL DEFAULT '深入'"),
        ("wild_training_reply_to_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("wild_training_reply_due_at", "REAL NOT NULL DEFAULT 0"),
        ("wild_training_retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("wild_training_last_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("wild_training_last_result", "TEXT NOT NULL DEFAULT ''"),
        ("wild_training_last_result_at", "REAL NOT NULL DEFAULT 0"),
        ("wild_training_last_completed_at", "REAL NOT NULL DEFAULT 0"),
        ("wild_training_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("stargazer_last_panel_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("stargazer_last_action", "TEXT NOT NULL DEFAULT ''"),
        ("stargazer_queued_action", "TEXT NOT NULL DEFAULT ''"),
        ("stargazer_idle_slot_count", "INTEGER NOT NULL DEFAULT 0"),
        ("stargazer_dim_slot_count", "INTEGER NOT NULL DEFAULT 0"),
        ("stargazer_ready_slot_count", "INTEGER NOT NULL DEFAULT 0"),
        ("stargazer_busy_until", "REAL NOT NULL DEFAULT 0"),
        ("stargazer_followup_due_at", "REAL NOT NULL DEFAULT 0"),
        ("stargazer_wait_full_collect", "INTEGER NOT NULL DEFAULT 0"),
        ("stargazer_collect_ready", "INTEGER NOT NULL DEFAULT 0"),
        ("stargazer_soothe_before_collect", "INTEGER NOT NULL DEFAULT 0"),
        ("guanxing_monitor_slot_key", "TEXT NOT NULL DEFAULT ''"),
        ("guanxing_monitor_slot_start_at", "REAL NOT NULL DEFAULT 0"),
        ("guanxing_monitor_slot_end_at", "REAL NOT NULL DEFAULT 0"),
        ("guanxing_monitor_seen_panel", "INTEGER NOT NULL DEFAULT 0"),
        ("guanxing_monitor_matched_keyword", "TEXT NOT NULL DEFAULT ''"),
        ("guanxing_monitor_matched_value", "TEXT NOT NULL DEFAULT ''"),
        ("guanxing_monitor_last_evolution_value", "TEXT NOT NULL DEFAULT ''"),
        ("guanxing_monitor_last_seen_at", "REAL NOT NULL DEFAULT 0"),
        ("guanxing_monitor_last_notified_slot_key", "TEXT NOT NULL DEFAULT ''"),
        ("guanxing_last_query_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("guanxing_last_panel_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("guanxing_panel_slot_key", "TEXT NOT NULL DEFAULT ''"),
        ("guanxing_last_panel_seen_at", "REAL NOT NULL DEFAULT 0"),
        ("guanxing_last_shift_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("guanxing_last_shift_slot_key", "TEXT NOT NULL DEFAULT ''"),
        ("guanxing_last_shift_target", "TEXT NOT NULL DEFAULT ''"),
        ("guanxing_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("last_formation_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("formation_pending_invite_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("formation_pending_assist_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("formation_last_action", "TEXT NOT NULL DEFAULT ''"),
        ("formation_last_result", "TEXT NOT NULL DEFAULT ''"),
        ("formation_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("formation_last_success_at", "REAL NOT NULL DEFAULT 0"),
        ("tianti_status_reply_to_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_last_status_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_last_status_seen_at", "REAL NOT NULL DEFAULT 0"),
        ("tianti_last_wenxin_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_last_climb_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_last_gangfeng_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_progress_current", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_progress_total", "INTEGER NOT NULL DEFAULT 12"),
        ("tianti_cycle_count", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_gangfeng_level", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_gangfeng_total", "INTEGER NOT NULL DEFAULT 12"),
        ("tianti_cooldown_text", "TEXT NOT NULL DEFAULT '未记录'"),
        ("tianti_wenxin_status", "TEXT NOT NULL DEFAULT '未记录'"),
        ("tianti_gangfeng_status", "TEXT NOT NULL DEFAULT '未记录'"),
        ("tianti_remaining_climb_count", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_last_wenxin_day", "TEXT NOT NULL DEFAULT ''"),
        ("tianti_wenxin_last_trigger_key", "TEXT NOT NULL DEFAULT ''"),
        ("tianti_gangfeng_last_trigger_key", "TEXT NOT NULL DEFAULT ''"),
        ("tianti_last_skip_reason", "TEXT NOT NULL DEFAULT ''"),
        ("tianti_theoretical_max_stage", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_wenxin_trigger_stage", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_last_cost_xiuwei", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_last_gain_xiuwei", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_last_gain_contrib", "INTEGER NOT NULL DEFAULT 0"),
        ("tianti_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("quiz_reply_to_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("quiz_chat_id", "INTEGER NOT NULL DEFAULT 0"),
        ("quiz_question", "TEXT NOT NULL DEFAULT ''"),
        ("quiz_options", "TEXT NOT NULL DEFAULT '{}' "),
        ("quiz_answer", "TEXT NOT NULL DEFAULT ''"),
        ("quiz_phase", "TEXT NOT NULL DEFAULT ''"),
        ("quiz_retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("quiz_match_mode", "TEXT NOT NULL DEFAULT ''"),
        ("quiz_answer_method", "TEXT NOT NULL DEFAULT ''"),
        ("quiz_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("quiz_last_matched_at", "REAL NOT NULL DEFAULT 0"),
        ("quiz_deadline_at", "REAL NOT NULL DEFAULT 0"),
        ("jiyin_reply_to_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("jiyin_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("nanlong_reply_to_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("nanlong_reply_due_at", "REAL NOT NULL DEFAULT 0"),
        ("nanlong_last_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("nanlong_retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("nanlong_last_command", "TEXT NOT NULL DEFAULT ''"),
        ("nanlong_protect_phase", "TEXT NOT NULL DEFAULT ''"),
        ("nanlong_place_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("nanlong_recall_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("nanlong_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("small_world_preach_reply_to_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_preach_due_at", "REAL NOT NULL DEFAULT 0"),
        ("small_world_god_cooldown_until", "REAL NOT NULL DEFAULT 0"),
        ("small_world_pending_god_action", "TEXT NOT NULL DEFAULT ''"),
        ("small_world_pending_god_reason", "TEXT NOT NULL DEFAULT ''"),
        ("small_world_pending_god_priority", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_pending_god_at", "REAL NOT NULL DEFAULT 0"),
        ("small_world_last_god_action", "TEXT NOT NULL DEFAULT ''"),
        ("small_world_last_god_sent_at", "REAL NOT NULL DEFAULT 0"),
        ("small_world_last_disaster_wave_at", "REAL NOT NULL DEFAULT 0"),
        ("small_world_barrier_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_barrier_due_at", "REAL NOT NULL DEFAULT 0"),
        ("small_world_last_barrier_sent_at", "REAL NOT NULL DEFAULT 0"),
        ("small_world_phase", "TEXT NOT NULL DEFAULT 'idle'"),
        ("small_world_query_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_manifest_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_manifest_cost_text", "TEXT NOT NULL DEFAULT ''"),
        ("small_world_harvest_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_refine_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_refresh_count", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_pending_incense", "REAL NOT NULL DEFAULT 0"),
        ("small_world_incense_stock", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_faith_value", "INTEGER NOT NULL DEFAULT 0"),
        ("small_world_panel_snapshot", "TEXT NOT NULL DEFAULT '{}' "),
        ("small_world_last_panel_at", "REAL NOT NULL DEFAULT 0"),
        ("small_world_last_public_request_at", "REAL NOT NULL DEFAULT 0"),
        ("small_world_last_public_harvest_at", "REAL NOT NULL DEFAULT 0"),
        ("small_world_next_public_harvest_at", "REAL NOT NULL DEFAULT 0"),
        ("small_world_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("resource_shortage_backoffs", "TEXT NOT NULL DEFAULT '{}' "),
        ("action_guard_sessions", "TEXT NOT NULL DEFAULT '{}' "),
        ("wendao_reply_to_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("wendao_reply_due_at", "REAL NOT NULL DEFAULT 0"),
        ("wendao_pending_result_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("wendao_sent_at", "REAL NOT NULL DEFAULT 0"),
        ("wendao_last_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("wendao_last_result", "TEXT NOT NULL DEFAULT ''"),
        ("wendao_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("mulan_phase", "TEXT NOT NULL DEFAULT 'idle'"),
        ("mulan_reply_to_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("mulan_reply_due_at", "REAL NOT NULL DEFAULT 0"),
        ("mulan_pending_ids", "TEXT NOT NULL DEFAULT ''"),
        ("mulan_report_texts", "TEXT NOT NULL DEFAULT '{}'"),
        ("mulan_report_day", "TEXT NOT NULL DEFAULT ''"),
        ("mulan_current_id", "INTEGER NOT NULL DEFAULT 0"),
        ("mulan_public_id", "INTEGER NOT NULL DEFAULT 0"),
        ("mulan_public_text", "TEXT NOT NULL DEFAULT ''"),
        ("mulan_support_action", "TEXT NOT NULL DEFAULT ''"),
        ("mulan_sent_at", "REAL NOT NULL DEFAULT 0"),
        ("mulan_last_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("mulan_last_command", "TEXT NOT NULL DEFAULT ''"),
        ("mulan_last_result", "TEXT NOT NULL DEFAULT ''"),
        ("mulan_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("mulan_cycle_count", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_target", "TEXT NOT NULL DEFAULT ''"),
        ("duel_total_count", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_completed_count", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_reserve_xiuwei", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_window_start_minute", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_window_end_minute", "INTEGER NOT NULL DEFAULT 1439"),
        ("duel_daily_limit_day", "TEXT NOT NULL DEFAULT ''"),
        ("duel_daily_limited_targets", "TEXT NOT NULL DEFAULT '[]'"),
        ("duel_log_reconcile_day", "TEXT NOT NULL DEFAULT ''"),
        ("duel_log_reconcile_at", "REAL NOT NULL DEFAULT 0"),
        ("duel_observed_completed_count", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_observed_baseline_count", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_observed_manual_count", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_observed_mind_remaining", "INTEGER NOT NULL DEFAULT -1"),
        ("duel_observed_last_command_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_reply_to_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_reply_due_at", "REAL NOT NULL DEFAULT 0"),
        ("duel_open_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_magic_due_at", "REAL NOT NULL DEFAULT 0"),
        ("duel_magic_sent_at", "REAL NOT NULL DEFAULT 0"),
        ("duel_started_at", "REAL NOT NULL DEFAULT 0"),
        ("duel_phaseful_retry_count", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_last_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("duel_last_result", "TEXT NOT NULL DEFAULT ''"),
        ("duel_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("duel_unequip_prepared", "INTEGER NOT NULL DEFAULT 0"),
        ("fishing_pond", "TEXT NOT NULL DEFAULT '青溪浅滩'"),
        ("fishing_bait", "TEXT NOT NULL DEFAULT '凡饵'"),
        ("fishing_daily_limit", "INTEGER NOT NULL DEFAULT 20"),
        ("fishing_daily_day", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_daily_count", "INTEGER NOT NULL DEFAULT 0"),
        ("fishing_daily_catch_summary_json", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_daily_summary_day", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_basket_calibrated_day", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_auto_chum_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ("fishing_chum_name", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_chum_names", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_chum_day", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_chum_counts", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_auto_buy_bait_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ("fishing_auto_buy_bait_count", "INTEGER NOT NULL DEFAULT 20"),
        ("fishing_auto_probe_enabled", "INTEGER NOT NULL DEFAULT 0"),
        ("fishing_auto_open_fish_enabled", "INTEGER NOT NULL DEFAULT 1"),
        ("fishing_cancel_after_sec", "INTEGER NOT NULL DEFAULT 120"),
        ("fishing_transfer_target_id", "INTEGER NOT NULL DEFAULT 0"),
        ("fishing_transfer_due_at", "REAL NOT NULL DEFAULT 0"),
        ("fishing_caught_fish_json", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_valuable_drop_reminders", "TEXT NOT NULL DEFAULT '[]'"),
        ("fishing_phase", "TEXT NOT NULL DEFAULT 'idle'"),
        ("fishing_reply_to_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("fishing_reply_due_at", "REAL NOT NULL DEFAULT 0"),
        ("fishing_status_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("fishing_pending_action", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_pending_open_fish", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_forced_buy_bait", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_forced_buy_count", "INTEGER NOT NULL DEFAULT 0"),
        ("fishing_started_at", "REAL NOT NULL DEFAULT 0"),
        ("fishing_active_chum_name", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_chum_rods_remaining", "INTEGER NOT NULL DEFAULT 0"),
        ("fishing_last_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("fishing_last_result", "TEXT NOT NULL DEFAULT ''"),
        ("fishing_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("explore_rift_reply_to_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("explore_rift_reply_due_at", "REAL NOT NULL DEFAULT 0"),
        ("explore_rift_pending_result_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("explore_rift_last_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("explore_rift_last_result", "TEXT NOT NULL DEFAULT ''"),
        ("explore_rift_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("explore_rift_last_result_key", "TEXT NOT NULL DEFAULT ''"),
        ("explore_rift_manual_required", "INTEGER NOT NULL DEFAULT 0"),
        ("explore_rift_nascent_escape_weak_until", "REAL NOT NULL DEFAULT 0"),
        ("explore_rift_rebirth_required", "INTEGER NOT NULL DEFAULT 0"),
        ("explore_rift_rebirth_phase", "TEXT NOT NULL DEFAULT 'idle'"),
        ("explore_rift_rebirth_due_at", "REAL NOT NULL DEFAULT 0"),
        ("explore_rift_rebirth_request_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("explore_rift_rebirth_options_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("explore_rift_rebirth_select_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("explore_rift_rebirth_options_text", "TEXT NOT NULL DEFAULT ''"),
        ("explore_rift_rebirth_selected_index", "INTEGER NOT NULL DEFAULT 0"),
        ("explore_rift_rebirth_last_result", "TEXT NOT NULL DEFAULT ''"),
        ("explore_rift_rebirth_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("explore_rift_rebirth_choice_mode", "TEXT NOT NULL DEFAULT 'safe_first'"),
        ("explore_rift_rebirth_preferred_root_type", "TEXT NOT NULL DEFAULT ''"),
        ("explore_rift_rebirth_preferred_attrs", "TEXT NOT NULL DEFAULT ''"),
        ("explore_rift_rebirth_blind_index", "INTEGER NOT NULL DEFAULT 1"),
        ("explore_rift_fatal_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("explore_rift_fatal_confirm_due_at", "REAL NOT NULL DEFAULT 0"),
        ("identity_info_reply_msg_ids", "TEXT NOT NULL DEFAULT '[]'"),
        ("last_identity_info_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("identity_info_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("identity_info_last_requested_at", "REAL NOT NULL DEFAULT 0"),
        ("identity_info_followup_due_at", "REAL NOT NULL DEFAULT 0"),
        ("identity_info_primary_payload", "TEXT NOT NULL DEFAULT '{}' "),
        ("second_soul_phase", "TEXT NOT NULL DEFAULT 'idle'"),
        ("second_soul_choice_strategy", "TEXT NOT NULL DEFAULT 'stable'"),
        ("second_soul_heart_demon_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("second_soul_heart_demon_notified", "INTEGER NOT NULL DEFAULT 0"),
        ("second_soul_status_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("second_soul_train_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("second_soul_last_train_started_at", "REAL NOT NULL DEFAULT 0"),
        ("second_soul_last_broadcast_key", "TEXT NOT NULL DEFAULT ''"),
        ("second_soul_last_broadcast_at", "REAL NOT NULL DEFAULT 0"),
        ("second_soul_moran_value", "INTEGER NOT NULL DEFAULT 0"),
        ("second_soul_purge_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("second_soul_purge_status_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("second_soul_purge_attempts", "INTEGER NOT NULL DEFAULT 0"),
        ("second_soul_purge_due_at", "REAL NOT NULL DEFAULT 0"),
        ("second_soul_purge_last_at", "REAL NOT NULL DEFAULT 0"),
        ("second_soul_last_error", "TEXT NOT NULL DEFAULT ''"),
        ("taiyi_yindao_element", "TEXT NOT NULL DEFAULT '水'"),
        ("taiyi_phase", "TEXT NOT NULL DEFAULT 'idle'"),
        ("taiyi_pending_node_name", "TEXT NOT NULL DEFAULT ''"),
        ("taiyi_yindao_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("taiyi_node_search_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("taiyi_node_define_msg_id", "INTEGER NOT NULL DEFAULT 0"),
        ("taiyi_freeze_reason", "TEXT NOT NULL DEFAULT ''"),
        ("taiyi_failure_history", "TEXT NOT NULL DEFAULT '[]'"),
        ("taiyi_yindao_resend_count", "INTEGER NOT NULL DEFAULT 0"),
        ("taiyi_search_resend_count", "INTEGER NOT NULL DEFAULT 0"),
        ("taiyi_last_error", "TEXT NOT NULL DEFAULT ''"),
    ),
    "pending_tasks": (
        ("chat_id", "INTEGER NOT NULL DEFAULT 0"),
        ("topic_id", "INTEGER NOT NULL DEFAULT 0"),
        ("priority", "TEXT NOT NULL DEFAULT ''"),
        ("source_module", "TEXT NOT NULL DEFAULT ''"),
        ("op_id", "TEXT NOT NULL DEFAULT ''"),
        ("chain_id", "TEXT NOT NULL DEFAULT ''"),
        ("delete_policy", "TEXT NOT NULL DEFAULT ''"),
    ),
}


# 依赖列已存在的数据回填。必须在补列之后执行。
def _run_schema_backfills(conn, *, added_columns):
    """Backfill columns whose value derives from older ones.

    Only runs for rows that actually need it, and only issues statements when
    there is something to migrate: `_ensure_schema_columns` is called on every
    startup, and the delta-save telemetry asserts that an unchanged database
    produces no mutating SQL at all.

    `added_columns` holds the columns this run just created, so a backfill tied
    to a brand-new column fires exactly once.
    """
    def _has_rows(sql):
        return conn.execute(f"SELECT EXISTS(SELECT 1 FROM ({sql}))").fetchone()[0]

    # 虚天侍妾碎片计数从旧的通用碎片列迁移过来
    pending_fragment = """
        SELECT 1 FROM identity_runtime_state
         WHERE COALESCE(concubine_fragment_xutian_count, 0) = 0
           AND COALESCE(concubine_fragment_count, 0) != 0
         LIMIT 1
    """
    if _has_rows(pending_fragment):
        conn.execute(
            """
            UPDATE identity_runtime_state
               SET concubine_fragment_xutian_count = concubine_fragment_count,
                   concubine_fragment_xutian_total = concubine_fragment_total
             WHERE COALESCE(concubine_fragment_xutian_count, 0) = 0
               AND COALESCE(concubine_fragment_count, 0) != 0
            """
        )

    # 野外历练完成时间此前只记录在 result_at 上；仅在该列首次建立时整体回填
    if ("identity_runtime_state", "wild_training_last_completed_at") in added_columns:
        conn.execute(
            """
            UPDATE identity_runtime_state
            SET wild_training_last_completed_at = wild_training_last_result_at
            WHERE wild_training_last_result_at > 0
              AND COALESCE(wild_training_last_completed_at, 0) <= 0
            """
        )

    # 结果编辑未留存的那批记录，完成时间要跟上最新一次结果时间
    pending_unsaved = """
        SELECT 1 FROM identity_runtime_state
         WHERE wild_training_last_result_at > COALESCE(wild_training_last_completed_at, 0)
           AND wild_training_last_result LIKE '结果编辑未留存%'
         LIMIT 1
    """
    if _has_rows(pending_unsaved):
        conn.execute(
            """
            UPDATE identity_runtime_state
            SET wild_training_last_completed_at = wild_training_last_result_at
            WHERE wild_training_last_result_at > COALESCE(wild_training_last_completed_at, 0)
              AND wild_training_last_result LIKE '结果编辑未留存%'
            """
        )


def _ensure_schema_columns(conn):
    """Bring an existing database up to the current column set.

    Adding a column is idempotent by construction: we read the table's current
    columns once and only issue ALTER for what is missing. A few defaults are
    computed from runtime config rather than literals, so they are applied
    separately below.
    """
    added_columns = set()
    for table, columns in _SCHEMA_COLUMNS.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, spec in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {spec}")
                added_columns.add((table, name))

    # 默认值取自运行期配置，不能写死成字面量
    module_columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_module_state)").fetchall()}
    if "divination_daily_limit" not in module_columns:
        conn.execute(
            "ALTER TABLE identity_module_state ADD COLUMN divination_daily_limit "
            f"INTEGER NOT NULL DEFAULT {int(DIVINATION_DEFAULT_DAILY_LIMIT)}"
        )
    pending_columns = {row[1] for row in conn.execute("PRAGMA table_info(pending_tasks)").fetchall()}
    if "max_retry" not in pending_columns:
        conn.execute(f"ALTER TABLE pending_tasks ADD COLUMN max_retry INTEGER NOT NULL DEFAULT {int(RETRY_LIMIT)}")

    _run_schema_backfills(conn, added_columns=added_columns)




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



def _meta_defaults():
    """Seed values written once, on first run, for every meta key.

    These were 38 hand-written `conn.execute("INSERT OR IGNORE INTO meta...")`
    calls, four lines each. Declared as data instead, adding a key is one line.
    Built lazily because one entry calls a helper defined later in this module.
    """
    return {
        "game_group_id": "-1002083016447",
        "game_group_route_config": _encode_meta_json({
            "enabled": True,
            "primary_group_id": -1002083016447,
            "backup_group_ids": [-1001680975844],
            "topic_id_by_group": {
                "-1002083016447": 0,
                "-1001680975844": 7310786,
            },
            "bot_reply_group_by_identity": {},
        }),
        "account_group_memberships": "{}",
        "game_bot_ids": "[-1003983937918, 7900199668, 8349385938, 8388633812, 8400307678, 8547797815, 8567800706, 8609885831, 8757550896]",
        "game_topic_id": "0",
        "forum_topics": "[]",
        "forum_topics_updated_at": "0",
        "auto_delete_sent_messages": "1",
        "global_enabled": "1",
        "tiandao_judgement_enabled": "0",
        "tiandao_judgement_pending": "{}",
        "tianji_quiz_pending": "{}",
        "divination_run_state": "{}",
        "world_boss_run_state": "{}",
        "world_boss_rotation_state": "{}",
        "guanxing_monitor_enabled": "0",
        "guanxing_monitor_targets": _encode_meta_json(["地磁暴动", "星辰异象"]),
        "guanxing_shift_target": "",
        "guanxing_shift_delay_sec": "10",
        "next_guanxing_monitor_notify_time": "0",
        "guanxing_monitor_slot_key": "",
        "guanxing_monitor_slot_start_at": "0",
        "guanxing_monitor_slot_end_at": "0",
        "guanxing_monitor_seen_panel": "0",
        "guanxing_monitor_matched_keyword": "",
        "guanxing_monitor_matched_value": "",
        "guanxing_monitor_last_evolution_value": "",
        "guanxing_monitor_last_seen_at": "0",
        "guanxing_monitor_last_notified_slot_key": "",
        "guanxing_round_state": "{}",
        "formation_run_state": "{}",
        "inventory_delta_records": "{}",
        "miniapp_state_records": "{}",
        "duel_target_cooldowns": "{}",
        "tree_miniapp_score_configs": "{}",
        "miniapp_auto_config": "{}",
        "quiz_learning_watchers": "{}",
        "quiz_ai_config": "{}",
        "accounts": "{}",
        "identity_account_map": "{}",
        "identity_membership_initialized": "0",
        "account_target_memberships": "{}",
    }


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
            username_aliases TEXT NOT NULL DEFAULT '[]',
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
            pet_formation_enabled INTEGER NOT NULL DEFAULT 0,
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
            mulan_enabled INTEGER NOT NULL DEFAULT 0,
            wanxin_enabled INTEGER NOT NULL DEFAULT 0,
            world_boss_enabled INTEGER NOT NULL DEFAULT 0,
            nanlong_enabled INTEGER NOT NULL DEFAULT 0,
            explore_rift_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_preach_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_manifest_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_harvest_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_refine_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_refresh_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_high_stock_silence_enabled INTEGER NOT NULL DEFAULT 0,
            small_world_barrier_enabled INTEGER NOT NULL DEFAULT 1,
            small_world_barrier_min_stock INTEGER NOT NULL DEFAULT 130000,
            small_world_barrier_guard_before_min INTEGER NOT NULL DEFAULT 30,
            small_world_barrier_min_interval_hours REAL NOT NULL DEFAULT 18,
            divination_enabled INTEGER NOT NULL DEFAULT 0,
            divination_daily_limit INTEGER NOT NULL DEFAULT 6,
            dungeon_join_enabled INTEGER NOT NULL DEFAULT 0,
            second_soul_enabled INTEGER NOT NULL DEFAULT 0,
            second_soul_auto_choice_enabled INTEGER NOT NULL DEFAULT 1,
            second_soul_purge_threshold INTEGER NOT NULL DEFAULT 60,
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
            next_pet_formation_time REAL NOT NULL DEFAULT 0,
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
            next_mulan_time REAL NOT NULL DEFAULT 0,
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
            pet_formation_last_error TEXT NOT NULL DEFAULT '',
            pet_formation_retry_count INTEGER NOT NULL DEFAULT 0,
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
            wild_training_last_completed_at REAL NOT NULL DEFAULT 0,
            wild_training_last_error TEXT NOT NULL DEFAULT '',
            wild_training_tianxing_prepare_retry_at REAL NOT NULL DEFAULT 0,
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
            quiz_deadline_at REAL NOT NULL DEFAULT 0,
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
            concubine_last_panel_chat_id INTEGER NOT NULL DEFAULT 0,
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
            wanxin_observation TEXT NOT NULL DEFAULT '{}',
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
            small_world_last_public_request_at REAL NOT NULL DEFAULT 0,
            small_world_last_public_harvest_at REAL NOT NULL DEFAULT 0,
            small_world_next_public_harvest_at REAL NOT NULL DEFAULT 0,
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
            explore_rift_tianxing_prepare_retry_at REAL NOT NULL DEFAULT 0,
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
            mulan_phase TEXT NOT NULL DEFAULT 'idle',
            mulan_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            mulan_reply_due_at REAL NOT NULL DEFAULT 0,
            mulan_pending_ids TEXT NOT NULL DEFAULT '',
            mulan_report_texts TEXT NOT NULL DEFAULT '{}',
            mulan_report_day TEXT NOT NULL DEFAULT '',
            mulan_current_id INTEGER NOT NULL DEFAULT 0,
            mulan_public_id INTEGER NOT NULL DEFAULT 0,
            mulan_public_text TEXT NOT NULL DEFAULT '',
            mulan_support_action TEXT NOT NULL DEFAULT '',
            mulan_sent_at REAL NOT NULL DEFAULT 0,
            mulan_last_msg_id INTEGER NOT NULL DEFAULT 0,
            mulan_last_command TEXT NOT NULL DEFAULT '',
            mulan_last_result TEXT NOT NULL DEFAULT '',
            mulan_last_error TEXT NOT NULL DEFAULT '',
            mulan_cycle_count INTEGER NOT NULL DEFAULT 0,
            duel_target TEXT NOT NULL DEFAULT '',
            duel_total_count INTEGER NOT NULL DEFAULT 0,
            duel_completed_count INTEGER NOT NULL DEFAULT 0,
            duel_reserve_xiuwei INTEGER NOT NULL DEFAULT 0,
            duel_window_start_minute INTEGER NOT NULL DEFAULT 0,
            duel_window_end_minute INTEGER NOT NULL DEFAULT 1439,
            duel_daily_limit_day TEXT NOT NULL DEFAULT '',
            duel_daily_limited_targets TEXT NOT NULL DEFAULT '[]',
            duel_log_reconcile_day TEXT NOT NULL DEFAULT '',
            duel_log_reconcile_at REAL NOT NULL DEFAULT 0,
            duel_observed_completed_count INTEGER NOT NULL DEFAULT 0,
            duel_observed_baseline_count INTEGER NOT NULL DEFAULT 0,
            duel_observed_manual_count INTEGER NOT NULL DEFAULT 0,
            duel_observed_mind_remaining INTEGER NOT NULL DEFAULT -1,
            duel_observed_last_command_msg_id INTEGER NOT NULL DEFAULT 0,
            duel_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            duel_reply_due_at REAL NOT NULL DEFAULT 0,
            duel_open_msg_id INTEGER NOT NULL DEFAULT 0,
            duel_magic_due_at REAL NOT NULL DEFAULT 0,
            duel_magic_sent_at REAL NOT NULL DEFAULT 0,
            duel_started_at REAL NOT NULL DEFAULT 0,
            duel_phaseful_retry_count INTEGER NOT NULL DEFAULT 0,
            duel_last_msg_id INTEGER NOT NULL DEFAULT 0,
            duel_last_result TEXT NOT NULL DEFAULT '',
            duel_last_error TEXT NOT NULL DEFAULT '',
            duel_unequip_prepared INTEGER NOT NULL DEFAULT 0,
            fishing_enabled INTEGER NOT NULL DEFAULT 0,
            next_fishing_time REAL NOT NULL DEFAULT 0,
            fishing_pond TEXT NOT NULL DEFAULT '青溪浅滩',
            fishing_bait TEXT NOT NULL DEFAULT '凡饵',
            fishing_daily_limit INTEGER NOT NULL DEFAULT 20,
            fishing_daily_day TEXT NOT NULL DEFAULT '',
            fishing_daily_count INTEGER NOT NULL DEFAULT 0,
            fishing_daily_catch_summary_json TEXT NOT NULL DEFAULT '',
            fishing_daily_summary_day TEXT NOT NULL DEFAULT '',
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
            chat_id INTEGER NOT NULL DEFAULT 0,
            topic_id INTEGER NOT NULL DEFAULT 0,
            max_retry INTEGER NOT NULL DEFAULT 1,
            priority TEXT NOT NULL DEFAULT '',
            source_module TEXT NOT NULL DEFAULT '',
            op_id TEXT NOT NULL DEFAULT '',
            chain_id TEXT NOT NULL DEFAULT '',
            delete_policy TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS command_attempts (
            op_id TEXT PRIMARY KEY,
            chain_id TEXT NOT NULL DEFAULT '',
            send_as_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL DEFAULT 0,
            source_module TEXT NOT NULL DEFAULT '',
            command TEXT NOT NULL DEFAULT '',
            command_family TEXT NOT NULL DEFAULT '',
            priority TEXT NOT NULL DEFAULT '',
            intent_json TEXT NOT NULL DEFAULT '{}',
            transport TEXT NOT NULL DEFAULT 'created',
            business TEXT NOT NULL DEFAULT 'open',
            recovery_policy TEXT NOT NULL DEFAULT 'wait_late_edit',
            block_code TEXT NOT NULL DEFAULT '',
            block_reason TEXT NOT NULL DEFAULT '',
            definitely_unsent INTEGER NOT NULL DEFAULT 0,
            root_msg_id INTEGER NOT NULL DEFAULT 0,
            reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            result_msg_id INTEGER NOT NULL DEFAULT 0,
            resend_count INTEGER NOT NULL DEFAULT 0,
            max_resend INTEGER NOT NULL DEFAULT 0,
            transport_due_at REAL NOT NULL DEFAULT 0,
            business_due_at REAL NOT NULL DEFAULT 0,
            business_code TEXT NOT NULL DEFAULT '',
            business_summary TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            last_transition_key TEXT NOT NULL DEFAULT '',
            meta_json TEXT NOT NULL DEFAULT '{}',
            version INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            sent_at REAL NOT NULL DEFAULT 0,
            closed_at REAL NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS command_attempt_transitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            op_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            axis TEXT NOT NULL,
            from_state TEXT NOT NULL DEFAULT '',
            to_state TEXT NOT NULL,
            code TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            transition_key TEXT NOT NULL,
            ts REAL NOT NULL,
            UNIQUE(op_id, seq),
            UNIQUE(op_id, transition_key)
        );

        CREATE TABLE IF NOT EXISTS command_attempt_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            op_id TEXT NOT NULL,
            seq INTEGER NOT NULL,
            kind TEXT NOT NULL,
            msg_id INTEGER NOT NULL DEFAULT 0,
            edit_seq INTEGER NOT NULL DEFAULT 0,
            family TEXT NOT NULL DEFAULT '',
            text_digest TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            idempotency_key TEXT NOT NULL,
            ts REAL NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(op_id, seq),
            UNIQUE(op_id, idempotency_key)
        );

        CREATE INDEX IF NOT EXISTS idx_command_attempts_identity_open
            ON command_attempts(send_as_id, business, transport, updated_at);
        CREATE INDEX IF NOT EXISTS idx_command_attempts_root_msg
            ON command_attempts(root_msg_id);
        CREATE INDEX IF NOT EXISTS idx_command_attempts_result_msg
            ON command_attempts(result_msg_id);
        CREATE INDEX IF NOT EXISTS idx_command_attempts_chain
            ON command_attempts(chain_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_command_attempts_due
            ON command_attempts(transport_due_at, business_due_at);
        CREATE INDEX IF NOT EXISTS idx_command_attempt_transitions_op
            ON command_attempt_transitions(op_id, seq);
        CREATE INDEX IF NOT EXISTS idx_command_attempt_evidence_msg
            ON command_attempt_evidence(msg_id);
        CREATE INDEX IF NOT EXISTS idx_command_attempt_evidence_op
            ON command_attempt_evidence(op_id, seq);

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
    for meta_key, meta_value in _meta_defaults().items():
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)", (meta_key, meta_value))
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


def _profile_persistence_snapshot(profile):
    return persistence_shadow.build_profile_snapshot(profile)


def _build_meta_persistence_snapshot():
    meta_snapshot = {
        key: encoder(getter())
        for key, (getter, encoder, _decoder) in _META_STATE_CODEC.items()
    }
    return meta_snapshot


def _build_identity_persistence_snapshot(send_as_id):
    return persistence_shadow.build_identity_snapshot(
        profile=get_send_as_profile(send_as_id),
        identity_state=get_identity_state(send_as_id),
        module_columns=tuple(IDENTITY_MODULE_COLUMNS),
        timer_columns=tuple(IDENTITY_TIMER_COLUMNS),
        runtime_columns=tuple(IDENTITY_RUNTIME_COLUMNS),
        serialize_value=_serialize_db_value,
        pending_command=get_pending_command,
        retry_limit=RETRY_LIMIT,
    )


def _build_persistence_snapshots():
    meta_snapshot = _build_meta_persistence_snapshot()
    identity_snapshots = {}
    for send_as_id in get_identity_ids():
        identity_snapshots[int(send_as_id)] = _build_identity_persistence_snapshot(send_as_id)
    return meta_snapshot, identity_snapshots


def _clear_persistence_snapshots():
    global _persistence_snapshot_db_key
    _persistence_snapshot_db_key = ""
    _persisted_meta_snapshot.clear()
    _persisted_identity_snapshots.clear()


def _record_persistence_snapshots(meta_snapshot, identity_snapshots):
    global _persistence_snapshot_db_key
    _persisted_meta_snapshot.clear()
    _persisted_meta_snapshot.update(dict(meta_snapshot or {}))
    _persisted_identity_snapshots.clear()
    _persisted_identity_snapshots.update(dict(identity_snapshots or {}))
    _persistence_snapshot_db_key = os.path.abspath(DB_FILE)


def _initialize_persistence_snapshots(meta_snapshot=None, identity_snapshots=None):
    if meta_snapshot is None or identity_snapshots is None:
        meta_snapshot, identity_snapshots = _build_persistence_snapshots()
    _record_persistence_snapshots(meta_snapshot, identity_snapshots)
    return meta_snapshot, identity_snapshots


def _read_live_guard_saved_at():
    try:
        with open(LIVE_GUARD_MANIFEST_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return float(payload.get("saved_at", 0) or 0) if isinstance(payload, dict) else 0.0
    except Exception:
        return 0.0


def _initialize_persistence_shadow(meta_snapshot=None, identity_snapshots=None):
    if not persistence_shadow.is_enabled():
        return
    try:
        if meta_snapshot is None or identity_snapshots is None:
            meta_snapshot, identity_snapshots = _build_persistence_snapshots()
    except Exception:
        persistence_shadow.safe_note_error()
        return
    persistence_shadow.safe_initialize(
        db_key=os.path.abspath(DB_FILE),
        meta_snapshot=meta_snapshot,
        identity_snapshots=identity_snapshots,
        initial_backup_at=_read_live_guard_saved_at(),
    )


def _capture_persistence_shadow(meta_snapshot=None, identity_snapshots=None):
    if not persistence_shadow.is_enabled():
        return None
    try:
        if meta_snapshot is None or identity_snapshots is None:
            meta_snapshot, identity_snapshots = _build_persistence_snapshots()
    except Exception:
        persistence_shadow.safe_note_error()
        return None
    return persistence_shadow.safe_capture(
        db_key=os.path.abspath(DB_FILE),
        meta_snapshot=meta_snapshot,
        identity_snapshots=identity_snapshots,
    )


def upsert_identity_to_db(send_as_id):
    conn = get_db_conn()
    _ensure_schema_columns_ready(conn)
    identity_state = get_identity_state(send_as_id)
    profile = get_send_as_profile(send_as_id)
    profile_values = _profile_persistence_snapshot(profile)
    now_ts = time.time()

    conn.execute(
        """
        INSERT INTO identities(
            send_as_id, username, username_aliases, label, daohao, realm, spiritual_root_type, spiritual_root_attrs, replica_professions, replica_gold_dps_enabled, pet_name, pet_warm_name, pet_trial_name, sect_name, sect_updated_at, jiyin_choice, nanlong_choice, stargazer_star_choice, tianti_rank_choice, stargazer_total_slots,
            checkin_window_start_hour_utc, checkin_window_end_hour_utc,
            tower_window_start_hour_utc, tower_window_end_hour_utc,
            enabled, xiuwei_current, xiuwei_max, battle_power_text, battle_power_value, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(send_as_id) DO UPDATE SET
            username=excluded.username,
            username_aliases=excluded.username_aliases,
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
            *profile_values,
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
            "INSERT OR REPLACE INTO pending_tasks(msg_id, send_as_id, cmd, sent_at, retry, timeout, reply_to_msg_id, chat_id, topic_id, max_retry, priority, source_module, op_id, chain_id, delete_policy) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(msg_id),
                int(send_as_id),
                get_pending_command(item),
                float(item.get("sent_at", 0) or 0),
                int(item.get("retry", 0) or 0),
                float(item.get("timeout", 0) or 0),
                int(item.get("reply_to_msg_id", 0) or 0),
                int(item.get("chat_id", 0) or 0),
                int(item.get("topic_id", 0) or 0),
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
        "SELECT username, username_aliases, label, daohao, realm, spiritual_root_type, spiritual_root_attrs, replica_professions, replica_gold_dps_enabled, pet_name, pet_warm_name, pet_trial_name, sect_name, sect_updated_at, jiyin_choice, nanlong_choice, stargazer_star_choice, tianti_rank_choice, stargazer_total_slots, checkin_window_start_hour_utc, checkin_window_end_hour_utc, tower_window_start_hour_utc, tower_window_end_hour_utc, enabled, xiuwei_current, xiuwei_max, battle_power_text, battle_power_value FROM identities WHERE send_as_id = ?",
        (int(send_as_id),),
    ).fetchone()
    if row:
        set_send_as_profile(
            send_as_id,
            row["username"],
            username_aliases=json.loads(row["username_aliases"] or "[]"),
            label=row["label"],
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
            "chat_id": row["chat_id"] if "chat_id" in row.keys() else 0,
            "topic_id": row["topic_id"] if "topic_id" in row.keys() else 0,
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
    "game_group_route_config": (
        get_game_group_route_config,
        _encode_meta_json,
        lambda value: set_game_group_route_config(_decode_meta_json(value, {})),
    ),
    "account_group_memberships": (
        get_account_group_memberships,
        _encode_meta_json,
        lambda value: set_account_group_memberships(_decode_meta_json(value, {})),
    ),
    "game_bot_ids": (
        get_game_bot_ids,
        _encode_meta_json,
        lambda value: set_game_bot_ids(_decode_meta_json(value, [])),
    ),
    "game_listener_account_ids": (
        get_game_listener_account_ids,
        _encode_meta_json,
        lambda value: set_game_listener_account_ids(_decode_meta_json(value, [])),
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
    "global_pause_source": (
        get_global_pause_source,
        lambda value: str(value or ""),
        lambda value: set_global_pause_source(str(value or "")),
    ),
    "global_recovery_hold_until": (
        get_global_recovery_hold_until,
        lambda value: str(float(value or 0)),
        lambda value: set_global_recovery_hold_until(_decode_meta_float(value, 0)),
    ),
    "global_recovery_throttle_until": (
        get_global_recovery_throttle_until,
        lambda value: str(float(value or 0)),
        lambda value: set_global_recovery_throttle_until(_decode_meta_float(value, 0)),
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
    "world_boss_rotation_state": (
        get_world_boss_rotation_state,
        _encode_meta_json,
        lambda value: set_world_boss_rotation_state(_decode_meta_json(value, {})),
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
    "channel_send_as_health": (
        get_channel_send_as_health,
        _encode_meta_json,
        lambda value: set_channel_send_as_health(_decode_meta_json(value, {})),
    ),
    "account_target_memberships": (
        get_account_target_memberships,
        _encode_meta_json,
        lambda value: set_account_target_memberships(_decode_meta_json(value, {})),
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
    "replica_kind_configs": (
        get_replica_kind_configs,
        _encode_meta_json,
        lambda value: set_replica_kind_configs(_decode_meta_json(value, {})),
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
    "replica_success_cooldown_hours": (
        get_replica_success_cooldown_hours,
        _encode_meta_json,
        lambda value: set_replica_success_cooldown_hours(_decode_meta_json(value, {})),
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
    "inventory_delta_records": (
        get_inventory_delta_records,
        _encode_meta_json,
        lambda value: set_inventory_delta_records(_decode_meta_json(value, {})),
    ),
    "miniapp_state_records": (
        get_miniapp_state_records,
        _encode_meta_json,
        lambda value: set_miniapp_state_records(_decode_meta_json(value, {})),
    ),
    "duel_target_cooldowns": (
        get_duel_target_cooldowns,
        _encode_meta_json,
        lambda value: set_duel_target_cooldowns(_decode_meta_json(value, {})),
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
    "mulan_intel_state": (
        lambda: _meta_state.get("mulan_intel_state") if isinstance(_meta_state.get("mulan_intel_state"), dict) else {},
        _encode_meta_json,
        lambda value: _set_meta_value("mulan_intel_state", _decode_meta_json(value, {})),
    ),
    "tree_miniapp_score_configs": (
        get_tree_miniapp_score_configs,
        _encode_meta_json,
        lambda value: set_tree_miniapp_score_configs(_decode_meta_json(value, {})),
    ),
    "miniapp_auto_config": (
        get_miniapp_auto_config,
        _encode_meta_json,
        lambda value: set_miniapp_auto_config(_decode_meta_json(value, {})),
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
    if not os.path.exists(_safety_watchdog_fused_file()):
        return
    rows = conn.execute(
        "SELECT key, value FROM meta WHERE key IN (?, ?)",
        ("global_enabled", "global_pause_source"),
    ).fetchall()
    meta = {str(row["key"]): str(row["value"] or "") for row in rows}
    db_enabled = meta.get("global_enabled")
    if db_enabled is None:
        return
    if str(db_enabled or "").strip() in {"0", "false", "False", ""}:
        set_global_enabled(False)
        pause_source = str(meta.get("global_pause_source") or get_global_pause_source() or "").strip()
        set_global_pause_source(pause_source or "safety_watchdog")


def _save_meta_state(conn, keys=None, snapshot=None):
    snapshot = dict(_build_meta_persistence_snapshot() if snapshot is None else snapshot)
    selected_keys = tuple(keys) if keys is not None else tuple(_META_STATE_CODEC)
    for key in selected_keys:
        if key not in _META_STATE_CODEC:
            continue
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (key, snapshot[key]),
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
        conn = _open_sqlite_conn(db_file, set_journal_mode=False)
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


def _read_live_guard_manifest():
    try:
        with open(LIVE_GUARD_MANIFEST_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _remove_sqlite_artifacts(db_file):
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.remove(str(db_file) + suffix)
        except FileNotFoundError:
            pass


def _remove_sqlite_sidecars(db_file):
    for suffix in ("-wal", "-shm", "-journal"):
        try:
            os.remove(str(db_file) + suffix)
        except FileNotFoundError:
            pass


def _sqlite_sidecars_exist(db_file):
    return any(
        os.path.exists(str(db_file) + suffix)
        for suffix in ("-wal", "-shm", "-journal")
    )


def _inspect_identity_db_file(db_file):
    result = {
        "exists": bool(db_file and os.path.exists(db_file)),
        "valid": False,
        "has_identity_table": False,
        "roster": [],
    }
    if not result["exists"]:
        return result
    conn = None
    try:
        conn = _open_sqlite_conn(db_file, set_journal_mode=False)
        rows = conn.execute("PRAGMA quick_check").fetchall()
        checks = [str(row[0] or "").strip().lower() for row in rows]
        if checks != ["ok"]:
            return result
        result["valid"] = True
        result["has_identity_table"] = bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='identities'"
            ).fetchone()
        )
        if result["has_identity_table"]:
            result["roster"] = _read_identity_roster_from_conn(conn)
        return result
    except Exception:
        return result
    finally:
        if conn is not None:
            conn.close()


def _validate_live_guard_db_file(db_file, *, expected_roster=None):
    inspection = _inspect_identity_db_file(db_file)
    if not inspection["valid"] or not inspection["has_identity_table"]:
        return []
    roster = inspection["roster"]
    if expected_roster is not None and list(roster) != list(expected_roster):
        return []
    return roster


def _live_guard_backup_reason(
    *,
    roster_changed=False,
    account_structure_changed=False,
    committed_change=False,
    now=None,
):
    if not committed_change:
        return ""
    if not os.path.exists(LIVE_GUARD_DB_FILE):
        return "bootstrap"
    if roster_changed:
        return "roster_changed"
    if account_structure_changed:
        return "account_structure_changed"
    manifest = _read_live_guard_manifest()
    try:
        saved_at = float(manifest.get("saved_at", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        saved_at = 0.0
    current = float(now if now is not None else time.time())
    if saved_at <= 0 or current - saved_at >= LIVE_GUARD_REFRESH_SEC:
        return "periodic"
    return ""


def _write_live_guard_backup(conn, *, reason="periodic"):
    if not _identity_collapse_guard_enabled():
        return False
    if os.environ.get("XIUXIAN_DISABLE_LIVE_DB_BACKUP") == "1":
        return False
    roster = _read_identity_roster_from_conn(conn)
    if not _roster_looks_like_live(roster):
        return False
    staging_db = LIVE_GUARD_DB_FILE + ".next"
    try:
        os.makedirs(LIVE_GUARD_DIR, exist_ok=True)
        now = time.time()
        identity_ids = [send_as_id for send_as_id, _username in roster]
        existing_manifest = _read_live_guard_manifest()
        _remove_sqlite_artifacts(staging_db)
        backup_conn = sqlite3.connect(staging_db, timeout=SQLITE_TIMEOUT_SEC)
        try:
            backup_conn.execute("PRAGMA journal_mode=DELETE")
            backup_conn.execute("PRAGMA synchronous=FULL")
            conn.backup(backup_conn)
            backup_conn.execute("PRAGMA journal_mode=DELETE")
            backup_conn.commit()
        finally:
            backup_conn.close()
        if _validate_live_guard_db_file(staging_db, expected_roster=roster) != roster:
            raise RuntimeError("live guard staging validation failed")

        try:
            previous_saved_at = float(existing_manifest.get("previous_saved_at", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            previous_saved_at = 0.0
        previous_reason = str(existing_manifest.get("previous_reason") or "")
        current_roster = _validate_live_guard_db_file(LIVE_GUARD_DB_FILE)
        rotated_current = False
        if _roster_looks_like_live(current_roster):
            _remove_sqlite_artifacts(LIVE_GUARD_PREVIOUS_DB_FILE)
            had_sidecars = _sqlite_sidecars_exist(LIVE_GUARD_DB_FILE)
            _remove_sqlite_sidecars(LIVE_GUARD_DB_FILE)
            if (
                had_sidecars
                and _validate_live_guard_db_file(
                    LIVE_GUARD_DB_FILE,
                    expected_roster=current_roster,
                ) != current_roster
            ):
                raise RuntimeError("live guard current generation changed after sidecar cleanup")
            os.replace(LIVE_GUARD_DB_FILE, LIVE_GUARD_PREVIOUS_DB_FILE)
            rotated_current = True
            try:
                previous_saved_at = float(existing_manifest.get("saved_at", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                previous_saved_at = 0.0
            previous_reason = str(existing_manifest.get("reason") or "")
        else:
            _remove_sqlite_artifacts(LIVE_GUARD_DB_FILE)

        try:
            os.replace(staging_db, LIVE_GUARD_DB_FILE)
        except Exception:
            if rotated_current and not os.path.exists(LIVE_GUARD_DB_FILE):
                shutil.copy2(LIVE_GUARD_PREVIOUS_DB_FILE, LIVE_GUARD_DB_FILE)
            raise
        previous_roster = (
            current_roster
            if rotated_current
            else _validate_live_guard_db_file(LIVE_GUARD_PREVIOUS_DB_FILE)
        )
        manifest = {
            "schema": 2,
            "saved_at": now,
            "reason": str(reason or "periodic"),
            "identity_count": len(roster),
            "identity_ids": identity_ids,
            "previous_available": _roster_looks_like_live(previous_roster),
            "previous_saved_at": previous_saved_at,
            "previous_reason": previous_reason,
        }
        tmp_manifest = LIVE_GUARD_MANIFEST_FILE + ".tmp"
        with open(tmp_manifest, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, sort_keys=True)
        os.replace(tmp_manifest, LIVE_GUARD_MANIFEST_FILE)
        return True
    finally:
        _remove_sqlite_artifacts(staging_db)


def _try_write_live_guard_backup(conn, *, reason):
    try:
        return _write_live_guard_backup(conn, reason=reason)
    except Exception:
        traceback.print_exc()
        return False


def _select_live_guard_restore_file():
    for backup_file in (LIVE_GUARD_DB_FILE, LIVE_GUARD_PREVIOUS_DB_FILE):
        roster = _validate_live_guard_db_file(backup_file)
        if _roster_looks_like_live(roster):
            return backup_file, roster
    return "", []


def _archive_sqlite_artifacts(db_file, archive_file):
    copied = False
    for suffix in ("", "-wal", "-shm", "-journal"):
        source = str(db_file) + suffix
        if not os.path.exists(source):
            continue
        shutil.copy2(source, str(archive_file) + suffix)
        copied = True
    return copied


def _maybe_restore_live_guard_backup():
    if not _identity_collapse_guard_enabled():
        return False
    if os.environ.get("XIUXIAN_DISABLE_LIVE_DB_RESTORE") == "1":
        return False
    if os.environ.get("XIUXIAN_ALLOW_IDENTITY_COLLAPSE") == "1":
        return False
    current_inspection = _inspect_identity_db_file(DB_FILE)
    current_roster = current_inspection["roster"]
    current_is_suspicious = bool(
        current_inspection["exists"]
        and (
            not current_inspection["valid"]
            or (
                current_inspection["has_identity_table"]
                and not current_roster
            )
            or _roster_looks_suspicious(current_roster)
        )
    )
    if not current_is_suspicious:
        return False
    backup_file, backup_roster = _select_live_guard_restore_file()
    if not backup_file:
        return False
    restore_tmp = DB_FILE + ".restore.tmp"
    try:
        if _db_conn is not None:
            _db_conn.close()
        backup_name = f"{DB_FILE}.suspicious-{int(time.time())}"
        _archive_sqlite_artifacts(DB_FILE, backup_name)
        _remove_sqlite_artifacts(restore_tmp)
        shutil.copy2(backup_file, restore_tmp)
        if _validate_live_guard_db_file(restore_tmp, expected_roster=backup_roster) != backup_roster:
            raise RuntimeError("live guard restore staging validation failed")
        _remove_sqlite_artifacts(DB_FILE)
        os.replace(restore_tmp, DB_FILE)
        print(
            "Restored live state DB from guard backup after suspicious roster: "
            f"current={len(current_roster)} backup={len(backup_roster)} "
            f"source={backup_file} saved_bad={backup_name}"
        )
        return True
    except Exception:
        traceback.print_exc()
        return False
    finally:
        _remove_sqlite_artifacts(restore_tmp)



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
    shadow_sample = None
    conn = None
    try:
        init_db()
        conn = get_db_conn()
        _ensure_schema_columns_ready(conn, verify=True)
        _sync_external_safety_pause_before_save(conn)
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

        current_meta_snapshot, current_identity_snapshots = _build_persistence_snapshots()
        shadow_sample = _capture_persistence_shadow(
            current_meta_snapshot,
            current_identity_snapshots,
        )
        snapshot_ready = _persistence_snapshot_db_key == os.path.abspath(DB_FILE)
        changed_meta_keys = tuple(
            key
            for key, value in current_meta_snapshot.items()
            if not snapshot_ready or _persisted_meta_snapshot.get(key) != value
        )
        changed_identity_ids = tuple(
            send_as_id
            for send_as_id in sorted(current_ids)
            if (
                not snapshot_ready
                or _persisted_identity_snapshots.get(send_as_id)
                != current_identity_snapshots[send_as_id]
            )
        )
        deleted_identity_ids = tuple(sorted(existing_ids - current_ids))

        if changed_meta_keys:
            _save_meta_state(conn, changed_meta_keys, snapshot=current_meta_snapshot)
        for send_as_id in deleted_identity_ids:
            delete_identity_from_db(send_as_id)
        for send_as_id in changed_identity_ids:
            upsert_identity_to_db(send_as_id)
        conn.commit()
        backup_reason = _live_guard_backup_reason(
            roster_changed=bool(deleted_identity_ids or current_ids - existing_ids),
            account_structure_changed=bool(
                {"accounts", "identity_account_map"}.intersection(changed_meta_keys)
            ),
            committed_change=bool(changed_meta_keys or changed_identity_ids or deleted_identity_ids),
        )
        if backup_reason:
            _try_write_live_guard_backup(conn, reason=backup_reason)
        persistence_shadow.safe_commit(shadow_sample)
        _record_persistence_snapshots(current_meta_snapshot, current_identity_snapshots)
        _state_dirty = False
        _last_flush_time = time.time()
        _mark_persistence_save_ok()
        return True
    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                traceback.print_exc()
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
        with _open_sqlite_conn(DB_FILE, row_factory=False, set_journal_mode=False) as conn:
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
        replica_kind_configs = {}
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
            if key == "replica_kind_configs":
                replica_kind_configs = _decode_meta_json(meta_map.get(key), {})
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
        set_replica_kind_configs(replica_kind_configs)

        if not membership_initialized:
            _save_meta_state(conn)
            for send_as_id in get_identity_ids():
                ensure_identity_registered(send_as_id)
                upsert_identity_to_db(send_as_id)
            conn.commit()

        meta_snapshot, identity_snapshots = _initialize_persistence_snapshots()
        _initialize_persistence_shadow(meta_snapshot, identity_snapshots)
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
    "open_db_connection",
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
