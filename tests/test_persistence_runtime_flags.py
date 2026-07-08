import copy
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import app
from model import persistence
from model import state as state_module
from model.features import tree


class RuntimeLogFlagPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._db_conn_snapshot = persistence._db_conn
        self._db_initialized_snapshot = persistence._db_initialized
        self._schema_columns_ensured_key_snapshot = persistence._schema_columns_ensured_key
        persistence._db_conn = None
        persistence._db_initialized = False
        persistence._schema_columns_ensured_key = None
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        if persistence._db_conn is not None:
            persistence._db_conn.close()
        persistence._db_conn = self._db_conn_snapshot
        persistence._db_initialized = self._db_initialized_snapshot
        persistence._schema_columns_ensured_key = self._schema_columns_ensured_key_snapshot
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _reset_persistence_connection(self):
        if persistence._db_conn is not None:
            persistence._db_conn.close()
        persistence._db_conn = None
        persistence._db_initialized = False
        persistence._schema_columns_ensured_key = None

    def test_state_db_connection_uses_busy_timeout_and_wal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                persistence.init_db()
                conn = persistence.get_db_conn()

                busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
                journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0] or "").lower()

        self.assertEqual(persistence.SQLITE_BUSY_TIMEOUT_MS, int(busy_timeout))
        self.assertEqual("wal", journal_mode)

    def test_divination_daily_limit_roundtrips_as_integer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                identity_id = 990000
                state_module.ensure_identity_registered(identity_id)
                state_module.set_divination_daily_limit(identity_id, 9)

                self.assertTrue(persistence.save_state())
                conn = persistence.get_db_conn()
                row = conn.execute(
                    "SELECT divination_daily_limit FROM identity_module_state WHERE send_as_id = ?",
                    (identity_id,),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(9, row["divination_daily_limit"])

                state_module._meta_state.clear()
                state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
                self._reset_persistence_connection()
                self.assertTrue(persistence.load_state())
                self.assertEqual(9, state_module.get_divination_daily_limit(identity_id))

    def test_game_listener_account_ids_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                state_module.set_game_listener_account_ids([301299112, "7538826434", 301299112, 0, "bad"])

                self.assertTrue(persistence.save_state())
                state_module._meta_state.clear()
                state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
                self._reset_persistence_connection()
                self.assertTrue(persistence.load_state())

        self.assertEqual([301299112, 7538826434], state_module.get_game_listener_account_ids())

    def test_tree_miniapp_score_configs_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                state_module.ensure_identity_registered(990030)
                state_module.ensure_identity_registered(990031)
                state_module.set_tree_miniapp_score_configs({
                    "990030": {
                        "jump": {"target_score_range": [28, 28]},
                        "fly": {"target_score_range": [36, 36]},
                    },
                    "990031": {
                        "jump": {"target_score_range": [44, 44]},
                        "fly": {"target_score_range": [52, 52]},
                    },
                })

                self.assertTrue(persistence.save_state())
                conn = persistence.get_db_conn()
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = ?",
                    ("tree_miniapp_score_configs",),
                ).fetchone()
                self.assertIsNotNone(row)
                self.assertIn("990030", row["value"])

                state_module._meta_state.clear()
                state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
                self._reset_persistence_connection()
                self.assertTrue(persistence.load_state())

        self.assertEqual(
            {
                "990030": {
                    "jump": {"target_score_range": (24, 32)},
                    "fly": {"target_score_range": (32, 40)},
                },
                "990031": {
                    "jump": {"target_score_range": (37, 45)},
                    "fly": {"target_score_range": (37, 45)},
                },
            },
            state_module.get_tree_miniapp_score_configs(),
        )

    def test_save_state_preserves_external_safety_watchdog_pause(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            state_dir = Path(db_path).parent
            with patch.object(persistence, "DB_FILE", db_path):
                persistence.init_db()
                conn = persistence.get_db_conn()
                conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('global_enabled', '0')")
                conn.commit()
                (state_dir / "safety_watchdog_fused.json").write_text(
                    '{"reason":"same command repeat"}',
                    encoding="utf-8",
                )
                state_module.set_global_enabled(True)

                self.assertTrue(persistence.save_state())
                value = conn.execute("SELECT value FROM meta WHERE key = 'global_enabled'").fetchone()["value"]

        self.assertEqual("0", value)
        self.assertFalse(state_module.get_global_enabled())

    def test_small_world_preach_legacy_default_normalization_is_one_time(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                persistence.init_db()
                conn = persistence.get_db_conn()
                for identity_id, enabled, preach, manifest in [
                    (990010, False, True, False),
                    (990011, True, True, False),
                    (990012, False, True, True),
                    (990013, False, False, False),
                ]:
                    state_module.ensure_identity_registered(identity_id)
                    with state_module.use_identity(identity_id):
                        state_module.state["small_world_enabled"] = enabled
                        state_module.state["small_world_preach_enabled"] = preach
                        state_module.state["small_world_manifest_enabled"] = manifest
                self.assertTrue(persistence.save_state())
                conn.execute(
                    "DELETE FROM meta WHERE key = ?",
                    (persistence.SMALL_WORLD_PREACH_DEFAULT_NORMALIZED_KEY,),
                )

                persistence._normalize_small_world_preach_defaults(conn)

                rows = conn.execute(
                    """
                    SELECT send_as_id, small_world_preach_enabled
                    FROM identity_module_state
                    WHERE send_as_id BETWEEN 990010 AND 990013
                    ORDER BY send_as_id
                    """
                ).fetchall()
                self.assertEqual(
                    {
                        990010: 0,
                        990011: 1,
                        990012: 1,
                        990013: 0,
                    },
                    {int(row["send_as_id"]): int(row["small_world_preach_enabled"]) for row in rows},
                )
                marker = conn.execute(
                    "SELECT value FROM meta WHERE key = ?",
                    (persistence.SMALL_WORLD_PREACH_DEFAULT_NORMALIZED_KEY,),
                ).fetchone()
                self.assertEqual("1", marker["value"])

                conn.execute(
                    "UPDATE identity_module_state SET small_world_preach_enabled = 1 WHERE send_as_id = ?",
                    (990010,),
                )
                persistence._normalize_small_world_preach_defaults(conn)
                row = conn.execute(
                    "SELECT small_world_preach_enabled FROM identity_module_state WHERE send_as_id = ?",
                    (990010,),
                ).fetchone()
                self.assertEqual(1, int(row["small_world_preach_enabled"]))

    def test_direct_runtime_upsert_migrates_missing_runtime_column(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                persistence.init_db()
                conn = persistence.get_db_conn()
                try:
                    conn.execute("ALTER TABLE identity_runtime_state DROP COLUMN fishing_cancel_after_sec")
                    conn.commit()
                except sqlite3.OperationalError as exc:
                    self.skipTest(f"sqlite DROP COLUMN unavailable: {exc}")

                persistence._schema_columns_ensured_key = None
                identity_id = 990020
                state_module.ensure_identity_registered(identity_id)
                with state_module.use_identity(identity_id):
                    state_module.state["fishing_cancel_after_sec"] = 180

                persistence.upsert_identity_to_db(identity_id)

                columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_runtime_state)").fetchall()}
                row = conn.execute(
                    "SELECT fishing_cancel_after_sec FROM identity_runtime_state WHERE send_as_id = ?",
                    (identity_id,),
                ).fetchone()
                self.assertIn("fishing_cancel_after_sec", columns)
                self.assertEqual(180, int(row["fishing_cancel_after_sec"]))

    def test_phaseful_log_dedupe_flags_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                identity_id = 990001
                state_module.ensure_identity_registered(identity_id)
                with state_module.use_identity(identity_id):
                    state_module.state["yuanying_waiting_logged"] = True
                    state_module.state["yuanying_protect_logged"] = True
                    state_module.state["deep_retreat_waiting_logged"] = True
                    state_module.state["deep_retreat_protect_logged"] = True
                    state_module.state["sect_teach_enabled"] = True
                    state_module.state["next_sect_teach_time"] = 1_700_000_555.0
                    state_module.state["sect_teach_reply_to_msg_id"] = 111
                    state_module.state["last_sect_teach_msg_id"] = 112
                    state_module.state["last_tower_command_sent_at"] = 1_700_000_120.0
                    state_module.state["tower_reply_due_at"] = 1_700_000_123.0
                    state_module.state["tower_retry_count"] = 1
                    state_module.state["concubine_greet_msg_id"] = 123
                    state_module.state["concubine_last_greet_day"] = "2026-05-24"
                    state_module.state["concubine_greet_retry_count"] = 1
                    state_module.state["concubine_greet_last_error"] = "今日已经问安过"
                    state_module.state["concubine_gift_status_msg_id"] = 234
                    state_module.state["concubine_gift_bag_msg_id"] = 345
                    state_module.state["concubine_gift_msg_id"] = 456
                    state_module.state["concubine_gift_amount"] = 60
                    state_module.state["concubine_last_gift_day"] = "2026-05-24"
                    state_module.state["concubine_gift_last_error"] = "灵石不足"
                    state_module.state["explore_rift_enabled"] = True
                    state_module.state["next_explore_rift_time"] = 1_700_000_666.0
                    state_module.state["explore_rift_reply_to_msg_id"] = 221
                    state_module.state["explore_rift_reply_due_at"] = 1_700_000_222.0
                    state_module.state["explore_rift_pending_result_msg_id"] = 223
                    state_module.state["explore_rift_last_msg_id"] = 224
                    state_module.state["explore_rift_last_result"] = "裂缝稳定"
                    state_module.state["explore_rift_last_error"] = "境界不足"
                    state_module.state["explore_rift_last_result_key"] = "rift:stable"
                    state_module.state["explore_rift_manual_required"] = True
                    state_module.state["explore_rift_rebirth_choice_mode"] = "root_first"
                    state_module.state["explore_rift_rebirth_preferred_root_type"] = "异灵根"
                    state_module.state["explore_rift_rebirth_preferred_attrs"] = "雷、冰"
                    state_module.state["explore_rift_rebirth_blind_index"] = 2
                    state_module.state["wendao_enabled"] = True
                    state_module.state["next_wendao_time"] = 1_700_000_777.0
                    state_module.state["wendao_reply_to_msg_id"] = 321
                    state_module.state["wendao_reply_due_at"] = 1_700_000_333.0
                    state_module.state["wendao_pending_result_msg_id"] = 654
                    state_module.state["wendao_sent_at"] = 1_700_000_300.0
                    state_module.state["wendao_last_msg_id"] = 655
                    state_module.state["wendao_last_result"] = "修为 +1000"
                    state_module.state["wendao_last_error"] = "冷却中"
                    state_module.state["formation_enabled"] = True
                    state_module.state["next_formation_time"] = 1_700_001_111.0
                    state_module.state["formation_cooldown_until"] = 1_700_043_200.0
                    state_module.state["last_formation_msg_id"] = 7897749
                    state_module.state["formation_pending_invite_msg_id"] = 7897745
                    state_module.state["formation_pending_assist_msg_id"] = 7897749
                    state_module.state["formation_last_action"] = "已助阵外部邀请"
                    state_module.state["formation_last_result"] = "布阵成功"
                    state_module.state["formation_last_error"] = "助阵回复超时"
                    state_module.state["formation_last_success_at"] = 1_700_000_999.0

                persistence.save_state()
                conn = persistence.get_db_conn()
                columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_runtime_state)").fetchall()}
                self.assertIn("yuanying_waiting_logged", columns)
                self.assertIn("yuanying_protect_logged", columns)
                self.assertIn("deep_retreat_waiting_logged", columns)
                self.assertIn("deep_retreat_protect_logged", columns)
                self.assertIn("sect_teach_reply_to_msg_id", columns)
                self.assertIn("last_sect_teach_msg_id", columns)
                self.assertIn("last_tower_command_sent_at", columns)
                self.assertIn("tower_reply_due_at", columns)
                self.assertIn("tower_retry_count", columns)
                self.assertIn("concubine_greet_msg_id", columns)
                self.assertIn("concubine_last_greet_day", columns)
                self.assertIn("concubine_greet_retry_count", columns)
                self.assertIn("concubine_greet_last_error", columns)
                self.assertIn("concubine_gift_status_msg_id", columns)
                self.assertIn("concubine_gift_bag_msg_id", columns)
                self.assertIn("concubine_gift_msg_id", columns)
                self.assertIn("concubine_gift_amount", columns)
                self.assertIn("concubine_last_gift_day", columns)
                self.assertIn("concubine_gift_last_error", columns)
                self.assertIn("explore_rift_reply_to_msg_id", columns)
                self.assertIn("explore_rift_reply_due_at", columns)
                self.assertIn("explore_rift_pending_result_msg_id", columns)
                self.assertIn("explore_rift_last_msg_id", columns)
                self.assertIn("explore_rift_last_result", columns)
                self.assertIn("explore_rift_last_error", columns)
                self.assertIn("explore_rift_last_result_key", columns)
                self.assertIn("explore_rift_manual_required", columns)
                self.assertIn("explore_rift_rebirth_choice_mode", columns)
                self.assertIn("explore_rift_rebirth_preferred_root_type", columns)
                self.assertIn("explore_rift_rebirth_preferred_attrs", columns)
                self.assertIn("explore_rift_rebirth_blind_index", columns)
                self.assertIn("wendao_reply_to_msg_id", columns)
                self.assertIn("wendao_reply_due_at", columns)
                self.assertIn("wendao_pending_result_msg_id", columns)
                self.assertIn("wendao_sent_at", columns)
                self.assertIn("wendao_last_msg_id", columns)
                self.assertIn("wendao_last_result", columns)
                self.assertIn("wendao_last_error", columns)
                self.assertIn("last_formation_msg_id", columns)
                self.assertIn("formation_pending_invite_msg_id", columns)
                self.assertIn("formation_pending_assist_msg_id", columns)
                self.assertIn("formation_last_action", columns)
                self.assertIn("formation_last_result", columns)
                self.assertIn("formation_last_error", columns)
                self.assertIn("formation_last_success_at", columns)
                module_columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_module_state)").fetchall()}
                timer_columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_timers)").fetchall()}
                self.assertIn("sect_teach_enabled", module_columns)
                self.assertIn("next_sect_teach_time", timer_columns)
                self.assertIn("explore_rift_enabled", module_columns)
                self.assertIn("next_explore_rift_time", timer_columns)
                self.assertIn("wendao_enabled", module_columns)
                self.assertIn("next_wendao_time", timer_columns)
                self.assertIn("formation_enabled", module_columns)
                self.assertIn("next_formation_time", timer_columns)
                self.assertIn("formation_cooldown_until", timer_columns)

                state_module._meta_state.clear()
                state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
                self._reset_persistence_connection()

                persistence.load_state()
                with state_module.use_identity(identity_id):
                    self.assertTrue(state_module.state["yuanying_waiting_logged"])
                    self.assertTrue(state_module.state["yuanying_protect_logged"])
                    self.assertTrue(state_module.state["deep_retreat_waiting_logged"])
                    self.assertTrue(state_module.state["deep_retreat_protect_logged"])
                    self.assertTrue(state_module.state["sect_teach_enabled"])
                    self.assertEqual(1_700_000_555.0, state_module.state["next_sect_teach_time"])
                    self.assertEqual(111, state_module.state["sect_teach_reply_to_msg_id"])
                    self.assertEqual(112, state_module.state["last_sect_teach_msg_id"])
                    self.assertEqual(1_700_000_120.0, state_module.state["last_tower_command_sent_at"])
                    self.assertEqual(1_700_000_123.0, state_module.state["tower_reply_due_at"])
                    self.assertEqual(1, state_module.state["tower_retry_count"])
                    self.assertEqual(123, state_module.state["concubine_greet_msg_id"])
                    self.assertEqual("2026-05-24", state_module.state["concubine_last_greet_day"])
                    self.assertEqual(1, state_module.state["concubine_greet_retry_count"])
                    self.assertEqual("今日已经问安过", state_module.state["concubine_greet_last_error"])
                    self.assertEqual(234, state_module.state["concubine_gift_status_msg_id"])
                    self.assertEqual(345, state_module.state["concubine_gift_bag_msg_id"])
                    self.assertEqual(456, state_module.state["concubine_gift_msg_id"])
                    self.assertEqual(60, state_module.state["concubine_gift_amount"])
                    self.assertEqual("2026-05-24", state_module.state["concubine_last_gift_day"])
                    self.assertEqual("灵石不足", state_module.state["concubine_gift_last_error"])
                    self.assertTrue(state_module.state["explore_rift_enabled"])
                    self.assertEqual(1_700_000_666.0, state_module.state["next_explore_rift_time"])
                    self.assertEqual(221, state_module.state["explore_rift_reply_to_msg_id"])
                    self.assertEqual(1_700_000_222.0, state_module.state["explore_rift_reply_due_at"])
                    self.assertEqual(223, state_module.state["explore_rift_pending_result_msg_id"])
                    self.assertEqual(224, state_module.state["explore_rift_last_msg_id"])
                    self.assertEqual("裂缝稳定", state_module.state["explore_rift_last_result"])
                    self.assertEqual("境界不足", state_module.state["explore_rift_last_error"])
                    self.assertEqual("rift:stable", state_module.state["explore_rift_last_result_key"])
                    self.assertTrue(state_module.state["explore_rift_manual_required"])
                    self.assertEqual("root_first", state_module.state["explore_rift_rebirth_choice_mode"])
                    self.assertEqual("异灵根", state_module.state["explore_rift_rebirth_preferred_root_type"])
                    self.assertEqual("雷、冰", state_module.state["explore_rift_rebirth_preferred_attrs"])
                    self.assertEqual(2, state_module.state["explore_rift_rebirth_blind_index"])
                    self.assertTrue(state_module.state["wendao_enabled"])
                    self.assertEqual(1_700_000_777.0, state_module.state["next_wendao_time"])
                    self.assertEqual(321, state_module.state["wendao_reply_to_msg_id"])
                    self.assertEqual(1_700_000_333.0, state_module.state["wendao_reply_due_at"])
                    self.assertEqual(654, state_module.state["wendao_pending_result_msg_id"])
                    self.assertEqual(1_700_000_300.0, state_module.state["wendao_sent_at"])
                    self.assertEqual(655, state_module.state["wendao_last_msg_id"])
                    self.assertEqual("修为 +1000", state_module.state["wendao_last_result"])
                    self.assertEqual("冷却中", state_module.state["wendao_last_error"])
                    self.assertTrue(state_module.state["formation_enabled"])
                    self.assertEqual(1_700_001_111.0, state_module.state["next_formation_time"])
                    self.assertEqual(1_700_043_200.0, state_module.state["formation_cooldown_until"])
                    self.assertEqual(7897749, state_module.state["last_formation_msg_id"])
                    self.assertEqual(7897745, state_module.state["formation_pending_invite_msg_id"])
                    self.assertEqual(7897749, state_module.state["formation_pending_assist_msg_id"])
                    self.assertEqual("已助阵外部邀请", state_module.state["formation_last_action"])
                    self.assertEqual("布阵成功", state_module.state["formation_last_result"])
                    self.assertEqual("助阵回复超时", state_module.state["formation_last_error"])
                    self.assertEqual(1_700_000_999.0, state_module.state["formation_last_success_at"])

    def test_global_runtime_meta_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                for identity_id in (990101, 990102, 990103):
                    state_module.ensure_identity_registered(identity_id)
                    state_module.update_send_as_profile(identity_id, username=f"user{identity_id}")
                state_module.set_replica_group_ids([-100777, -100888])
                state_module.set_replica_listener_account_map({"-100777": 7001, "-100888": 7002})
                state_module.set_replica_dispatch_group_ids([-100999])
                state_module.set_replica_dispatch_listener_account_map({"-100999": 7003})
                state_module.set_replica_participant_identity_ids([990101, 990103, 123])
                state_module.set_replica_dispatch_participant_identity_ids([990101, 123])
                state_module.set_replica_kind_configs({
                    "cangkun": {
                        "enabled": False,
                        "participant_identity_ids": [990103, 123],
                        "dispatch_participant_identity_ids": [990101],
                    },
                })
                state_module.set_replica_virtual_hall_match_enabled_map({"-100777": "true", "-100888": "false"})
                state_module.set_replica_query_aggregator_config({
                    "base_url": "https://example.invalid/api/",
                    "client_id": "client-a",
                    "secret": "secret-a",
                })
                state_module.set_replica_success_cooldown_hours({"cangkun": 3.25})
                state_module.set_replica_run_state({"room": {"status": "active"}})
                state_module.set_formation_run_state({
                    "active_invites": {"7897745": {"msg_id": 7897745, "owner_username": "@david"}},
                    "attempted_assists": {"990101": {"7897745": {"status": "sent"}}},
                    "last_success": {"at": 1_700_000_000.0},
                })
                state_module.set_dungeon_join_run_state({"990101": {"room_id": "R1"}})
                state_module.state["dungeon_quiet_until"] = 12345.0
                state_module.state["dungeon_quiet_reason"] = "坠魔谷静场令"
                state_module.state["dungeon_quiet_last_log_at"] = 12000.0

                self.assertTrue(persistence.save_state())

                state_module._meta_state.clear()
                state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
                self._reset_persistence_connection()
                self.assertTrue(persistence.load_state())

                self.assertEqual([-100777, -100888], state_module.get_replica_group_ids())
                self.assertEqual({"-100777": 7001, "-100888": 7002}, state_module.get_replica_listener_account_map())
                self.assertEqual([-100999], state_module.get_replica_dispatch_group_ids())
                self.assertEqual({"-100999": 7003}, state_module.get_replica_dispatch_listener_account_map())
                self.assertEqual([990101, 990103], state_module.get_replica_participant_identity_ids())
                self.assertEqual([990101], state_module.get_replica_dispatch_participant_identity_ids())
                cangkun_config = state_module.get_replica_kind_config("cangkun")
                self.assertFalse(cangkun_config["enabled"])
                self.assertEqual([990103], cangkun_config["participant_identity_ids"])
                self.assertEqual([990101], cangkun_config["dispatch_participant_identity_ids"])
                self.assertEqual({"-100777": True, "-100888": False}, state_module.get_replica_virtual_hall_match_enabled_map())
                self.assertEqual(
                    {
                        "base_url": "https://example.invalid/api",
                        "client_id": "client-a",
                        "secret": "secret-a",
                    },
                    state_module.get_replica_query_aggregator_config(),
                )
                self.assertEqual({"cangkun": 3.25}, state_module.get_replica_success_cooldown_hours())
                self.assertEqual({"room": {"status": "active"}}, state_module.get_replica_run_state())
                self.assertEqual(
                    {
                        "active_invites": {"7897745": {"msg_id": 7897745, "owner_username": "@david"}},
                        "attempted_assists": {"990101": {"7897745": {"status": "sent"}}},
                        "last_success": {"at": 1_700_000_000.0},
                    },
                    state_module.get_formation_run_state(),
                )
                self.assertEqual({"990101": {"room_id": "R1"}}, state_module.get_dungeon_join_run_state())
                self.assertEqual(12345.0, state_module.state["dungeon_quiet_until"])
                self.assertEqual("坠魔谷静场令", state_module.state["dungeon_quiet_reason"])
                self.assertEqual(12000.0, state_module.state["dungeon_quiet_last_log_at"])

    def test_has_persisted_identity_rows_detects_existing_roster(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                self.assertFalse(persistence.has_persisted_identity_rows())
                identity_id = 990151
                state_module.ensure_identity_registered(identity_id)
                state_module.update_send_as_profile(identity_id, username="persisted")

                self.assertTrue(persistence.save_state())
                self.assertTrue(persistence.has_persisted_identity_rows())

    def test_has_persisted_identity_rows_treats_unreadable_db_as_existing_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.db"
            db_path.write_text("not sqlite", encoding="utf-8")
            with patch.object(persistence, "DB_FILE", str(db_path)):
                self.assertTrue(persistence.has_persisted_identity_rows())

    def test_save_state_records_persistence_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "state.db"
            db_path.write_text("not sqlite", encoding="utf-8")
            with patch.object(persistence, "DB_FILE", str(db_path)):
                state_module.ensure_identity_registered(990181)
                self.assertFalse(persistence.save_state())
                self.assertTrue(persistence.has_persistence_write_failure())
                failure = persistence.get_persistence_write_failure()
                self.assertGreater(failure["failed_at"], 0)
                self.assertTrue(failure["error"])

    def test_pending_task_send_intent_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                identity_id = 990201
                state_module.ensure_identity_registered(identity_id)
                with state_module.use_identity(identity_id):
                    state_module.state["pending_tasks"] = {
                        4567: {
                            "cmd": ".引道 水",
                            "sent_at": 123.0,
                            "retry": 0,
                            "timeout": 60.0,
                            "reply_to_msg_id": 0,
                            "max_retry": 1,
                            "priority": "chain",
                            "source_module": "太一",
                            "op_id": "taiyi-yindao-4567",
                            "chain_id": "taiyi-cycle-1",
                            "delete_policy": "auto_delete",
                        }
                    }

                self.assertTrue(persistence.save_state())
                conn = persistence.get_db_conn()
                columns = {row[1] for row in conn.execute("PRAGMA table_info(pending_tasks)").fetchall()}
                self.assertTrue({"source_module", "op_id", "chain_id", "delete_policy"}.issubset(columns))

                state_module._meta_state.clear()
                state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
                self._reset_persistence_connection()
                self.assertTrue(persistence.load_state())

                with state_module.use_identity(identity_id):
                    item = state_module.state["pending_tasks"][4567]
                    self.assertEqual("太一", item["source_module"])
                    self.assertEqual("taiyi-yindao-4567", item["op_id"])
                    self.assertEqual("taiyi-cycle-1", item["chain_id"])
                    self.assertEqual("auto_delete", item["delete_policy"])

    def test_load_state_tolerates_runtime_column_list_newer_than_db_row(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                identity_id = 990301
                state_module.ensure_identity_registered(identity_id)
                state_module.update_send_as_profile(identity_id, username="futurecol")
                state_module._meta_state["identity_membership_initialized"] = True
                with state_module.use_identity(identity_id):
                    state_module.state["ranch_last_result"] = "ok"

                self.assertTrue(persistence.save_state())

                state_module._meta_state.clear()
                state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
                self._reset_persistence_connection()
                runtime_columns = list(persistence.IDENTITY_RUNTIME_COLUMNS) + ["future_runtime_col"]
                with patch.object(persistence, "IDENTITY_RUNTIME_COLUMNS", runtime_columns):
                    self.assertTrue(persistence.load_state())

                with state_module.use_identity(identity_id):
                    self.assertEqual("ok", state_module.state["ranch_last_result"])

    def test_tree_pulse_panel_state_persists_for_ui_status(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                identity_id = 990341
                state_module.ensure_identity_registered(identity_id)
                state_module.update_send_as_profile(identity_id, username="treepulse")
                state_module._meta_state["identity_membership_initialized"] = True
                with state_module.use_identity(identity_id):
                    state_module.state["tree_enabled"] = True
                    state_module.state["next_irr_time"] = 1_700_000_600.0
                    state_module.state["tree_pulse_mode_seen"] = True
                    state_module.state["tree_pulse_last_panel_at"] = 1_700_000_000.0
                    state_module.state["tree_pulse_progress"] = 72.5
                    state_module.state["tree_pulse_main"] = "木"
                    state_module.state["tree_pulse_aux"] = "水"
                    state_module.state["tree_pulse_reverse"] = "火"
                    state_module.state["tree_pulse_neutral"] = "土/金"
                    state_module.state["tree_pulse_stability"] = 62
                    state_module.state["tree_pulse_stability_max"] = 100
                    state_module.state["tree_pulse_turbidity"] = 3
                    state_module.state["tree_pulse_turbidity_max"] = 165
                    state_module.state["tree_pulse_daily_used"] = 1
                    state_module.state["tree_pulse_daily_limit"] = 6
                    state_module.state["tree_pulse_rush_used"] = 0
                    state_module.state["tree_pulse_rush_limit"] = 2
                    state_module.state["tree_pulse_last_action"] = ".定脉 固脉 土"
                    state_module.state["tree_pulse_last_error"] = "脉稳偏低"

                self.assertTrue(persistence.save_state())
                conn = persistence.get_db_conn()
                columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_runtime_state)").fetchall()}
                self.assertIn("tree_pulse_mode_seen", columns)
                self.assertIn("tree_pulse_last_error", columns)

                state_module._meta_state.clear()
                state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
                self._reset_persistence_connection()
                self.assertTrue(persistence.load_state())

                with state_module.use_identity(identity_id):
                    self.assertTrue(state_module.state["tree_pulse_mode_seen"])
                    self.assertEqual("木", state_module.state["tree_pulse_main"])
                    self.assertEqual(62, state_module.state["tree_pulse_stability"])
                    self.assertEqual("脉稳偏低", state_module.state["tree_pulse_last_error"])
                    status_text = tree.get_tree_status_text()

                self.assertIn("当前玩法：云梦灵眼定脉", status_text)
                self.assertIn("进度：72.50%", status_text)
                self.assertIn("今日定脉：1/6", status_text)
                self.assertIn("脉稳：62/100；浊息/紊乱：3/165", status_text)

    def test_save_state_migrates_existing_runtime_table_before_upsert(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                identity_id = 990351
                state_module.ensure_identity_registered(identity_id)
                state_module.update_send_as_profile(identity_id, username="oldruntime")
                with state_module.use_identity(identity_id):
                    state_module.state["last_tower_msg_id"] = 7001

                self.assertTrue(persistence.save_state())
                conn = persistence.get_db_conn()
                conn.execute("ALTER TABLE identity_runtime_state DROP COLUMN tower_reply_due_at")
                conn.execute("ALTER TABLE identity_runtime_state DROP COLUMN tower_retry_count")
                conn.commit()

                with state_module.use_identity(identity_id):
                    state_module.state["tower_reply_due_at"] = 1_700_000_321.0
                    state_module.state["tower_retry_count"] = 1

                self.assertTrue(persistence.save_state())
                columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_runtime_state)").fetchall()}
                self.assertIn("tower_reply_due_at", columns)
                self.assertIn("tower_retry_count", columns)
                row = conn.execute(
                    "SELECT tower_reply_due_at, tower_retry_count FROM identity_runtime_state WHERE send_as_id = ?",
                    (identity_id,),
                ).fetchone()
                self.assertEqual(1_700_000_321.0, row["tower_reply_due_at"])
                self.assertEqual(1, row["tower_retry_count"])

    def test_runtime_migration_backfills_wild_training_completed_anchor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                identity_id = 990352
                result_at = 1_700_000_123.0
                state_module.ensure_identity_registered(identity_id)
                state_module.update_send_as_profile(identity_id, username="wildold")

                self.assertTrue(persistence.save_state())
                conn = persistence.get_db_conn()
                conn.execute(
                    """
                    UPDATE identity_runtime_state
                    SET wild_training_last_result = '修为+12000',
                        wild_training_last_result_at = ?
                    WHERE send_as_id = ?
                    """,
                    (result_at, identity_id),
                )
                conn.execute("ALTER TABLE identity_runtime_state DROP COLUMN wild_training_last_completed_at")
                conn.commit()

                persistence._schema_columns_ensured_key = None
                persistence._ensure_schema_columns(conn)

                columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_runtime_state)").fetchall()}
                self.assertIn("wild_training_last_completed_at", columns)
                row = conn.execute(
                    "SELECT wild_training_last_completed_at FROM identity_runtime_state WHERE send_as_id = ?",
                    (identity_id,),
                ).fetchone()
                self.assertEqual(result_at, row["wild_training_last_completed_at"])

    def test_runtime_migration_refreshes_missing_wild_training_result_anchor(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                identity_id = 990353
                result_at = 1_700_000_456.0
                old_completed_at = 1_700_000_123.0
                state_module.ensure_identity_registered(identity_id)
                state_module.update_send_as_profile(identity_id, username="wildmissing")

                self.assertTrue(persistence.save_state())
                conn = persistence.get_db_conn()
                conn.execute(
                    """
                    UPDATE identity_runtime_state
                    SET wild_training_last_result = '结果编辑未留存，已按正常周期恢复，原消息ID=42',
                        wild_training_last_result_at = ?,
                        wild_training_last_completed_at = ?
                    WHERE send_as_id = ?
                    """,
                    (result_at, old_completed_at, identity_id),
                )
                conn.commit()

                persistence._schema_columns_ensured_key = None
                persistence._ensure_schema_columns(conn)

                row = conn.execute(
                    "SELECT wild_training_last_completed_at FROM identity_runtime_state WHERE send_as_id = ?",
                    (identity_id,),
                ).fetchone()
                self.assertEqual(result_at, row["wild_training_last_completed_at"])

    def test_save_state_blocks_demo_identity_collapse_over_live_roster(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            guard_dir = Path(tmpdir) / "guard"
            with patch.object(persistence, "DB_FILE", db_path), patch.dict(
                "os.environ",
                {
                    "XIUXIAN_ENFORCE_IDENTITY_GUARD": "1",
                    "XIUXIAN_ALLOW_IDENTITY_COLLAPSE": "0",
                    "XIUXIAN_DISABLE_LIVE_DB_BACKUP": "1",
                    "XIUXIAN_LIVE_GUARD_DIR": str(guard_dir),
                },
            ):
                for identity_id in range(990001, 990013):
                    state_module.ensure_identity_registered(identity_id)
                    state_module.update_send_as_profile(identity_id, username=f"live{identity_id}")

                self.assertTrue(persistence.save_state())
                conn = persistence.get_db_conn()
                self.assertEqual(12, conn.execute("SELECT count(*) FROM identities").fetchone()[0])

                state_module._meta_state["identity_ids"] = []
                state_module._meta_state["identity_states"] = {}
                state_module._meta_state["send_as_profiles"] = {}
                state_module.ensure_identity_registered(991201)
                state_module.update_send_as_profile(991201, username="leader")

                self.assertFalse(persistence.save_state())
                self.assertEqual(12, conn.execute("SELECT count(*) FROM identities").fetchone()[0])
                self.assertIsNone(conn.execute("SELECT 1 FROM identities WHERE send_as_id = 991201").fetchone())

    def test_load_state_restores_last_good_db_when_demo_db_replaces_live_db(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            guard_dir = Path(tmpdir) / "guard"
            guard_db = str(guard_dir / "chaogu_state.last-good.db")
            guard_manifest = str(guard_dir / "manifest.json")
            with patch.object(persistence, "DB_FILE", db_path), \
                 patch.object(persistence, "LIVE_GUARD_DIR", str(guard_dir)), \
                 patch.object(persistence, "LIVE_GUARD_DB_FILE", guard_db), \
                 patch.object(persistence, "LIVE_GUARD_MANIFEST_FILE", guard_manifest), \
                 patch.dict(
                    "os.environ",
                    {
                        "XIUXIAN_ENFORCE_IDENTITY_GUARD": "1",
                        "XIUXIAN_ALLOW_IDENTITY_COLLAPSE": "0",
                    },
                 ):
                for identity_id in range(990001, 990013):
                    state_module.ensure_identity_registered(identity_id)
                    state_module.update_send_as_profile(identity_id, username=f"live{identity_id}")

                self.assertTrue(persistence.save_state())
                self.assertTrue(Path(guard_db).exists())

                self._reset_persistence_connection()
                Path(db_path).unlink()
                state_module._meta_state.clear()
                state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
                state_module.ensure_identity_registered(991201)
                state_module.update_send_as_profile(991201, username="leader")
                self.assertTrue(persistence.save_state())

                state_module._meta_state.clear()
                state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
                self._reset_persistence_connection()
                self.assertTrue(persistence.load_state())

                self.assertEqual(12, len(state_module.get_identity_ids()))
                self.assertNotIn(991201, state_module.get_identity_ids())
                self.assertTrue(list(Path(tmpdir).glob("state.db.suspicious-*")))


class StartupStateLoadSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    async def test_bootstrap_refuses_first_init_when_existing_state_load_fails(self):
        with (
            patch.object(app, "load_state", return_value=False),
            patch.object(app, "has_persisted_identity_rows", return_value=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "状态加载失败"):
                await app.bootstrap()

    async def test_bootstrap_skips_runtime_recovery_when_global_paused(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        state_module.ensure_identity_registered(990701)
        state_module.set_global_enabled(False)

        class FakeClient:
            async def connect(self):
                return None

            def is_connected(self):
                return False

        with (
            patch.object(app, "load_state", return_value=True),
            patch.object(app, "get_accounts", return_value={}),
            patch.object(app, "client", FakeClient()),
            patch.object(app, "get_all_clients", return_value={}),
            patch.object(app, "start_ui_server", new=AsyncMock()) as ui_mock,
            patch.object(app, "run_startup_account_integrity_check", return_value={"audit_lines": []}),
            patch.object(app, "initialize_identity_runtime") as init_mock,
            patch.object(app, "scan_startup_timeout_tasks") as scan_mock,
            patch.object(app, "clear_transient_send_failures_for_global_recovery") as clear_mock,
            patch.object(app, "spread_overdue_runtime_timers") as spread_mock,
            patch.object(app, "save_state") as save_mock,
        ):
            await app.bootstrap()

        ui_mock.assert_awaited_once()
        init_mock.assert_not_called()
        scan_mock.assert_not_called()
        clear_mock.assert_not_called()
        spread_mock.assert_not_called()
        save_mock.assert_not_called()

    async def test_bootstrap_clears_transient_send_failures_before_recovery_spread(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        state_module.ensure_identity_registered(990702)
        state_module.set_global_enabled(True)
        with state_module.use_identity(990702):
            state_module.state["next_pet_time"] = 1_700_000_000.0

        class FakeClient:
            async def connect(self):
                return None

            def is_connected(self):
                return False

        call_order = []

        def clear_side_effect(now):
            call_order.append("clear")
            return 1

        def spread_side_effect(now, *, reason):
            call_order.append("spread")
            return 1

        with (
            patch.object(app, "load_state", return_value=True),
            patch.object(app, "get_accounts", return_value={}),
            patch.object(app, "client", FakeClient()),
            patch.object(app, "get_all_clients", return_value={}),
            patch.object(app, "start_ui_server", new=AsyncMock()) as ui_mock,
            patch.object(app, "run_startup_account_integrity_check", return_value={"audit_lines": []}),
            patch.object(app, "restore_guanxing_round_runtime", return_value=({}, False)),
            patch.object(app, "scan_startup_timeout_tasks", return_value={"closed_count": 0, "affected_identity_ids": [], "alerts": []}) as scan_mock,
            patch.object(app, "initialize_identity_runtime") as init_mock,
            patch.object(app, "clear_transient_send_failures_for_global_recovery", side_effect=clear_side_effect) as clear_mock,
            patch.object(app, "spread_overdue_runtime_timers", side_effect=spread_side_effect) as spread_mock,
            patch.object(app, "save_state") as save_mock,
            patch.object(app, "send_audit_log", new=lambda *args, **kwargs: "audit"),
            patch.object(app, "_fire_and_forget") as fire_mock,
        ):
            await app.bootstrap()

        ui_mock.assert_awaited_once()
        scan_mock.assert_called_once()
        init_mock.assert_called_once_with(990702, ANY)
        clear_mock.assert_called_once()
        spread_mock.assert_called_once()
        save_mock.assert_called_once()
        fire_mock.assert_called_once_with("audit")
        self.assertEqual(["clear", "spread"], call_order)


if __name__ == "__main__":
    unittest.main()
