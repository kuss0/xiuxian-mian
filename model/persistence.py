import json
import sqlite3
import time
import traceback

from .config import DB_FILE, DB_SCHEMA_VERSION, FLUSH_INTERVAL_SEC
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
    get_guanxing_monitor_enabled,
    get_guanxing_round_state,
    get_identity_ids,
    get_identity_state,
    get_send_as_profile,
    new_identity_state,
    set_auto_delete_sent_messages,
    set_forum_topics,
    set_global_enabled,
    set_game_bot_ids,
    get_quiz_learning_watchers,
    set_game_group_id,
    set_game_topic_id,
    set_guanxing_monitor_enabled,
    set_guanxing_round_state,
    set_quiz_learning_watchers,
    set_send_as_profile,
    get_accounts,
    set_accounts,
    get_identity_account_map,
    set_identity_account_map,
    _meta_state,
)
from .timing import configure_timing

_db_conn = None
_db_initialized = False
_state_dirty = False
_last_flush_time = 0


def get_db_conn():
    global _db_conn
    if _db_conn is None:
        _db_conn = sqlite3.connect(DB_FILE)
        _db_conn.row_factory = sqlite3.Row
    return _db_conn


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
    if "nanlong_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN nanlong_enabled INTEGER NOT NULL DEFAULT 0")
    if "guanxing_monitor_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN guanxing_monitor_enabled INTEGER NOT NULL DEFAULT 0")
    if "guanxing_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN guanxing_enabled INTEGER NOT NULL DEFAULT 0")
    if "last_guanxing_done_day" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN last_guanxing_done_day TEXT NOT NULL DEFAULT ''")
    if "tianti_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN tianti_enabled INTEGER NOT NULL DEFAULT 0")
    if "tianti_wenxin_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN tianti_wenxin_enabled INTEGER NOT NULL DEFAULT 1")
    if "tianti_gangfeng_enabled" not in module_columns:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN tianti_gangfeng_enabled INTEGER NOT NULL DEFAULT 1")

    identity_columns = {row[1] for row in conn.execute("PRAGMA table_info(identities)").fetchall()}
    if "pet_name" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN pet_name TEXT NOT NULL DEFAULT ''")
    if "daohao" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN daohao TEXT NOT NULL DEFAULT ''")
    if "realm" not in identity_columns:
        conn.execute("ALTER TABLE identities ADD COLUMN realm TEXT NOT NULL DEFAULT ''")
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

    timer_columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_timers)").fetchall()}
    if "next_quiz_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_quiz_time REAL NOT NULL DEFAULT 0")
    if "next_jiyin_time" not in timer_columns:
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_jiyin_time REAL NOT NULL DEFAULT 0")
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

    runtime_columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_runtime_state)").fetchall()}
    if "stargazer_last_panel_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN stargazer_last_panel_msg_id INTEGER NOT NULL DEFAULT 0")
    if "stargazer_last_action" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN stargazer_last_action TEXT NOT NULL DEFAULT ''")
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
    if "tianti_status_reply_to_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_status_reply_to_msg_id INTEGER NOT NULL DEFAULT 0")
    if "tianti_last_status_msg_id" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN tianti_last_status_msg_id INTEGER NOT NULL DEFAULT 0")
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
    if "quiz_question" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_question TEXT NOT NULL DEFAULT ''")
    if "quiz_options" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_options TEXT NOT NULL DEFAULT '{}' ")
    if "quiz_answer" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN quiz_answer TEXT NOT NULL DEFAULT ''")
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
    if "nanlong_last_error" not in runtime_columns:
        conn.execute("ALTER TABLE identity_runtime_state ADD COLUMN nanlong_last_error TEXT NOT NULL DEFAULT ''")
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



def _migrate_schema_to_current(conn):
    _ensure_schema_columns(conn)
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
            pet_name TEXT NOT NULL DEFAULT '',
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
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS identity_module_state (
            send_as_id INTEGER PRIMARY KEY,
            tree_enabled INTEGER NOT NULL,
            pet_enabled INTEGER NOT NULL,
            stargazer_enabled INTEGER NOT NULL DEFAULT 0,
            guanxing_monitor_enabled INTEGER NOT NULL DEFAULT 0,
            guanxing_enabled INTEGER NOT NULL DEFAULT 0,
            tianti_enabled INTEGER NOT NULL DEFAULT 0,
            tianti_wenxin_enabled INTEGER NOT NULL DEFAULT 1,
            tianti_gangfeng_enabled INTEGER NOT NULL DEFAULT 1,
            quiz_enabled INTEGER NOT NULL,
            jiyin_enabled INTEGER NOT NULL DEFAULT 0,
            nanlong_enabled INTEGER NOT NULL DEFAULT 0,
            yuanying_enabled INTEGER NOT NULL,
            deep_retreat_enabled INTEGER NOT NULL,
            checkin_enabled INTEGER NOT NULL,
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
            next_nanlong_time REAL NOT NULL DEFAULT 0,
            next_yuanying_time REAL NOT NULL,
            next_deep_retreat_time REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS identity_runtime_state (
            send_as_id INTEGER PRIMARY KEY,
            sect_teach_reply_to_msg_id INTEGER NOT NULL,
            last_checkin_msg_id INTEGER NOT NULL,
            last_sect_teach_msg_id INTEGER NOT NULL,
            checkin_cleanup_msg_ids TEXT NOT NULL,
            last_tower_msg_id INTEGER NOT NULL,
            stargazer_last_panel_msg_id INTEGER NOT NULL DEFAULT 0,
            stargazer_last_action TEXT NOT NULL DEFAULT '',
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
            tianti_status_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            tianti_last_status_msg_id INTEGER NOT NULL DEFAULT 0,
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
            quiz_question TEXT NOT NULL DEFAULT '',
            quiz_options TEXT NOT NULL DEFAULT '{}',
            quiz_answer TEXT NOT NULL DEFAULT '',
            quiz_last_error TEXT NOT NULL DEFAULT '',
            quiz_last_matched_at REAL NOT NULL DEFAULT 0,
            jiyin_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            jiyin_last_error TEXT NOT NULL DEFAULT '',
            nanlong_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
            nanlong_reply_due_at REAL NOT NULL DEFAULT 0,
            nanlong_last_error TEXT NOT NULL DEFAULT '',
            yuanying_phase TEXT NOT NULL,
            yuanying_probe_pending INTEGER NOT NULL,
            yuanying_summary_sent_at REAL NOT NULL,
            last_yuanying_summary_msg_id INTEGER NOT NULL,
            last_yuanying_command_time REAL NOT NULL,
            deep_retreat_phase TEXT NOT NULL,
            deep_retreat_probe_pending INTEGER NOT NULL,
            deep_retreat_summary_sent_at REAL NOT NULL,
            last_deep_retreat_summary_msg_id INTEGER NOT NULL,
            last_deep_retreat_command_time REAL NOT NULL,
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
            reply_to_msg_id INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS message_index (
            msg_id INTEGER PRIMARY KEY,
            send_as_id INTEGER NOT NULL,
            sent_at REAL NOT NULL,
            kind TEXT NOT NULL DEFAULT 'command'
        );
        """
    )
    current_schema_version = _get_schema_version(conn)
    if current_schema_version < DB_SCHEMA_VERSION:
        _migrate_schema_to_current(conn)
    else:
        _ensure_schema_columns(conn)
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("game_group_id", "-1001680975844"),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
        ("game_bot_ids", "[8388633812]"),
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
        ("guanxing_monitor_enabled", "0"),
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
        ("quiz_learning_watchers", "{}"),
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
    identity_state = get_identity_state(send_as_id)
    profile = get_send_as_profile(send_as_id)
    now_ts = time.time()

    conn.execute(
        """
        INSERT INTO identities(
            send_as_id, username, label, daohao, realm, pet_name, sect_name, sect_updated_at, jiyin_choice, nanlong_choice, stargazer_star_choice, tianti_rank_choice, stargazer_total_slots,
            checkin_window_start_hour_utc, checkin_window_end_hour_utc,
            tower_window_start_hour_utc, tower_window_end_hour_utc,
            enabled, xiuwei_current, xiuwei_max, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(send_as_id) DO UPDATE SET
            username=excluded.username,
            label=excluded.label,
            daohao=excluded.daohao,
            realm=excluded.realm,
            pet_name=excluded.pet_name,
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
            updated_at=excluded.updated_at
        """,
        (
            int(send_as_id),
            profile.get("username", "") or "",
            profile.get("label", "") or "",
            profile.get("daohao", "") or "",
            profile.get("realm", "") or "",
            profile.get("pet_name", "") or "",
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
            "INSERT OR REPLACE INTO pending_tasks(msg_id, send_as_id, cmd, sent_at, retry, timeout, reply_to_msg_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                int(msg_id),
                int(send_as_id),
                item.get("cmd", ""),
                float(item.get("sent_at", 0) or 0),
                int(item.get("retry", 0) or 0),
                float(item.get("timeout", 0) or 0),
                int(item.get("reply_to_msg_id", 0) or 0),
            ),
        )

    conn.execute("DELETE FROM message_index WHERE send_as_id = ?", (int(send_as_id),))
    for msg_id, sent_at in identity_state.get("my_msg_ids", {}).items():
        conn.execute(
            "INSERT OR REPLACE INTO message_index(msg_id, send_as_id, sent_at, kind) VALUES (?, ?, ?, ?)",
            (int(msg_id), int(send_as_id), float(sent_at or 0), "command"),
        )


def _load_identity_from_db(send_as_id):
    conn = get_db_conn()
    identity_state = new_identity_state()

    row = conn.execute(
        "SELECT username, label, daohao, realm, pet_name, sect_name, sect_updated_at, jiyin_choice, nanlong_choice, stargazer_star_choice, tianti_rank_choice, stargazer_total_slots, checkin_window_start_hour_utc, checkin_window_end_hour_utc, tower_window_start_hour_utc, tower_window_end_hour_utc, enabled, xiuwei_current, xiuwei_max FROM identities WHERE send_as_id = ?",
        (int(send_as_id),),
    ).fetchone()
    if row:
        set_send_as_profile(
            send_as_id,
            row["username"],
            row["label"],
            daohao=row["daohao"],
            realm=row["realm"],
            pet_name=row["pet_name"],
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
        )

    row = conn.execute("SELECT * FROM identity_module_state WHERE send_as_id = ?", (int(send_as_id),)).fetchone()
    if row:
        for col in IDENTITY_MODULE_COLUMNS:
            identity_state[col] = _deserialize_db_value(col, row[col])

    row = conn.execute("SELECT * FROM identity_timers WHERE send_as_id = ?", (int(send_as_id),)).fetchone()
    if row:
        for col in IDENTITY_TIMER_COLUMNS:
            identity_state[col] = _deserialize_db_value(col, row[col])

    row = conn.execute("SELECT * FROM identity_runtime_state WHERE send_as_id = ?", (int(send_as_id),)).fetchone()
    if row:
        for col in IDENTITY_RUNTIME_COLUMNS:
            identity_state[col] = _deserialize_db_value(col, row[col])

    pending_rows = conn.execute("SELECT * FROM pending_tasks WHERE send_as_id = ?", (int(send_as_id),)).fetchall()
    identity_state["pending_tasks"] = {
        int(row["msg_id"]): {
            "cmd": row["cmd"],
            "sent_at": row["sent_at"],
            "retry": row["retry"],
            "timeout": row["timeout"],
            "reply_to_msg_id": row["reply_to_msg_id"],
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
    "guanxing_monitor_enabled": (
        get_guanxing_monitor_enabled,
        lambda value: "1" if value else "0",
        lambda value: set_guanxing_monitor_enabled(_decode_meta_bool_flag(value, False)),
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
    "quiz_learning_watchers": (
        get_quiz_learning_watchers,
        _encode_meta_json,
        lambda value: set_quiz_learning_watchers(_decode_meta_json(value, {})),
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


def _save_meta_state(conn):
    for key, (getter, encoder, _) in _META_STATE_CODEC.items():
        conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
            (key, encoder(getter())),
        )



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
        _save_meta_state(conn)
        existing_ids = {
            int(row["send_as_id"])
            for row in conn.execute("SELECT send_as_id FROM identities").fetchall()
        }
        current_ids = {int(send_as_id) for send_as_id in get_identity_ids()}
        for send_as_id in sorted(existing_ids - current_ids):
            delete_identity_from_db(send_as_id)
        for send_as_id in get_identity_ids():
            upsert_identity_to_db(send_as_id)
        conn.commit()
        _state_dirty = False
        _last_flush_time = time.time()
    except Exception:
        traceback.print_exc()


def mark_dirty():
    global _state_dirty
    _state_dirty = True


def flush_if_dirty(now=None):
    if not _state_dirty:
        return
    if now is None:
        now = time.time()
    if now - _last_flush_time >= FLUSH_INTERVAL_SEC:
        save_state()


def load_state():
    try:
        init_db()
        conn = get_db_conn()

        meta_rows = conn.execute("SELECT key, value FROM meta").fetchall()
        meta_map = {str(row["key"] or ""): row["value"] for row in meta_rows}
        forum_topics = []
        forum_topics_updated_at = 0
        membership_initialized = False
        for key, (_, _, decoder) in _META_STATE_CODEC.items():
            decoded = decoder(meta_map.get(key))
            if key == "forum_topics":
                forum_topics = decoded
            elif key == "forum_topics_updated_at":
                forum_topics_updated_at = decoded
            elif key == "identity_membership_initialized":
                membership_initialized = bool(decoded)
        set_forum_topics(forum_topics, updated_at=forum_topics_updated_at)
        rows = conn.execute(
            "SELECT send_as_id, username, label, daohao, realm, pet_name, sect_name, sect_updated_at, stargazer_star_choice, tianti_rank_choice, stargazer_total_slots, checkin_window_start_hour_utc, checkin_window_end_hour_utc, tower_window_start_hour_utc, tower_window_end_hour_utc, enabled, xiuwei_current, xiuwei_max FROM identities ORDER BY send_as_id"
        ).fetchall()

        _meta_state["identity_ids"] = []
        _meta_state["identity_states"] = {}
        for row in rows:
            send_as_id = int(row["send_as_id"])
            _meta_state["identity_ids"].append(send_as_id)
            _load_identity_from_db(send_as_id)

        if not membership_initialized:
            _save_meta_state(conn)
            for send_as_id in get_identity_ids():
                ensure_identity_registered(send_as_id)
                upsert_identity_to_db(send_as_id)
            conn.commit()

        return True
    except Exception:
        traceback.print_exc()
        return False


configure_timing(save_state)


__all__ = [
    "flush_if_dirty",
    "get_db_conn",
    "init_db",
    "load_state",
    "delete_identity_from_db",
    "mark_dirty",
    "save_quiz_learning_watchers_state",
    "save_state",
    "upsert_identity_to_db",
]
