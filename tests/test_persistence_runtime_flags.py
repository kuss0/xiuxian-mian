import copy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import persistence
from model import state as state_module


class RuntimeLogFlagPersistenceTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._db_conn_snapshot = persistence._db_conn
        self._db_initialized_snapshot = persistence._db_initialized
        persistence._db_conn = None
        persistence._db_initialized = False
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        if persistence._db_conn is not None:
            persistence._db_conn.close()
        persistence._db_conn = self._db_conn_snapshot
        persistence._db_initialized = self._db_initialized_snapshot
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _reset_persistence_connection(self):
        if persistence._db_conn is not None:
            persistence._db_conn.close()
        persistence._db_conn = None
        persistence._db_initialized = False

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

                persistence.save_state()
                conn = persistence.get_db_conn()
                columns = {row[1] for row in conn.execute("PRAGMA table_info(identity_runtime_state)").fetchall()}
                self.assertIn("yuanying_waiting_logged", columns)
                self.assertIn("yuanying_protect_logged", columns)
                self.assertIn("deep_retreat_waiting_logged", columns)
                self.assertIn("deep_retreat_protect_logged", columns)
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

                state_module._meta_state.clear()
                state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
                self._reset_persistence_connection()

                persistence.load_state()
                with state_module.use_identity(identity_id):
                    self.assertTrue(state_module.state["yuanying_waiting_logged"])
                    self.assertTrue(state_module.state["yuanying_protect_logged"])
                    self.assertTrue(state_module.state["deep_retreat_waiting_logged"])
                    self.assertTrue(state_module.state["deep_retreat_protect_logged"])
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

    def test_global_runtime_meta_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                for identity_id in (990101, 990102, 990103):
                    state_module.ensure_identity_registered(identity_id)
                    state_module.update_send_as_profile(identity_id, username=f"user{identity_id}")
                state_module.set_replica_group_ids([-100777, -100888])
                state_module.set_replica_listener_account_map({"-100777": 7001, "-100888": 7002})
                state_module.set_replica_participant_identity_ids([990101, 990103, 123])
                state_module.set_replica_virtual_hall_match_enabled_map({"-100777": "true", "-100888": "false"})
                state_module.set_replica_query_aggregator_config({
                    "base_url": "https://example.invalid/api/",
                    "client_id": "client-a",
                    "secret": "secret-a",
                })
                state_module.set_replica_run_state({"room": {"status": "active"}})
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
                self.assertEqual([990101, 990103], state_module.get_replica_participant_identity_ids())
                self.assertEqual({"-100777": True, "-100888": False}, state_module.get_replica_virtual_hall_match_enabled_map())
                self.assertEqual(
                    {
                        "base_url": "https://example.invalid/api",
                        "client_id": "client-a",
                        "secret": "secret-a",
                    },
                    state_module.get_replica_query_aggregator_config(),
                )
                self.assertEqual({"room": {"status": "active"}}, state_module.get_replica_run_state())
                self.assertEqual({"990101": {"room_id": "R1"}}, state_module.get_dungeon_join_run_state())
                self.assertEqual(12345.0, state_module.state["dungeon_quiet_until"])
                self.assertEqual("坠魔谷静场令", state_module.state["dungeon_quiet_reason"])
                self.assertEqual(12000.0, state_module.state["dungeon_quiet_last_log_at"])

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


if __name__ == "__main__":
    unittest.main()
