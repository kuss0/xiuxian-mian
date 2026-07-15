import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model import persistence
from model import state as state_module


def _mutating_statements(statements):
    prefixes = ("INSERT", "UPDATE", "DELETE", "REPLACE")
    return [statement for statement in statements if statement.lstrip().upper().startswith(prefixes)]


class PersistenceDeltaLabTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._db_conn_snapshot = persistence._db_conn
        self._db_initialized_snapshot = persistence._db_initialized
        self._schema_snapshot = persistence._schema_columns_ensured_key
        self._schema_version_snapshot = persistence._schema_columns_ensured_version
        self._snapshot_db_key = persistence._persistence_snapshot_db_key
        self._meta_snapshot = copy.deepcopy(persistence._persisted_meta_snapshot)
        self._identity_snapshots = copy.deepcopy(persistence._persisted_identity_snapshots)
        persistence._db_conn = None
        persistence._db_initialized = False
        persistence._schema_columns_ensured_key = None
        persistence._schema_columns_ensured_version = None
        persistence._clear_persistence_snapshots()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        if persistence._db_conn is not None:
            persistence._db_conn.close()
        persistence._db_conn = self._db_conn_snapshot
        persistence._db_initialized = self._db_initialized_snapshot
        persistence._schema_columns_ensured_key = self._schema_snapshot
        persistence._schema_columns_ensured_version = self._schema_version_snapshot
        persistence._persistence_snapshot_db_key = self._snapshot_db_key
        persistence._persisted_meta_snapshot.clear()
        persistence._persisted_meta_snapshot.update(self._meta_snapshot)
        persistence._persisted_identity_snapshots.clear()
        persistence._persisted_identity_snapshots.update(self._identity_snapshots)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _save_without_guard_backup(self):
        with patch.object(persistence, "_write_live_guard_backup"):
            return persistence.save_state()

    def test_repeated_no_change_save_has_no_mutating_sql(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990101)
            state_module.ensure_identity_registered(990102)
            self.assertTrue(self._save_without_guard_backup())

            statements = []
            conn = persistence.get_db_conn()
            conn.set_trace_callback(statements.append)
            self.assertTrue(self._save_without_guard_backup())
            conn.set_trace_callback(None)

        self.assertEqual([], _mutating_statements(statements))

    def test_load_initializes_snapshot_for_no_change_save(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990105)
            self.assertTrue(self._save_without_guard_backup())
            persistence.get_db_conn().close()
            persistence._db_conn = None
            persistence._db_initialized = False
            persistence._schema_columns_ensured_key = None
            persistence._schema_columns_ensured_version = None
            persistence._clear_persistence_snapshots()
            state_module._meta_state.clear()
            state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
            self.assertTrue(persistence.load_state())

            statements = []
            conn = persistence.get_db_conn()
            conn.set_trace_callback(statements.append)
            self.assertTrue(self._save_without_guard_backup())
            conn.set_trace_callback(None)

        self.assertEqual([], _mutating_statements(statements))

    def test_identity_change_writes_only_changed_identity(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990111)
            state_module.ensure_identity_registered(990112)
            self.assertTrue(self._save_without_guard_backup())
            with state_module.use_identity(990111):
                state_module.state["tower_retry_count"] = 2

            original = persistence.upsert_identity_to_db
            calls = []

            def record(identity_id):
                calls.append(int(identity_id))
                return original(identity_id)

            with patch.object(persistence, "upsert_identity_to_db", side_effect=record):
                self.assertTrue(self._save_without_guard_backup())

            row = persistence.get_db_conn().execute(
                "SELECT tower_retry_count FROM identity_runtime_state WHERE send_as_id = ?",
                (990111,),
            ).fetchone()

        self.assertEqual([990111], calls)
        self.assertEqual(2, int(row["tower_retry_count"]))

    def test_meta_only_change_does_not_rewrite_identities(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990121)
            self.assertTrue(self._save_without_guard_backup())
            state_module.set_global_recovery_hold_until(1_800_000_000.0)

            with patch.object(persistence, "upsert_identity_to_db", wraps=persistence.upsert_identity_to_db) as upsert_mock:
                self.assertTrue(self._save_without_guard_backup())

            value = persistence.get_db_conn().execute(
                "SELECT value FROM meta WHERE key = ?",
                ("global_recovery_hold_until",),
            ).fetchone()["value"]

        upsert_mock.assert_not_called()
        self.assertEqual("1800000000.0", value)

    def test_failed_identity_write_keeps_old_snapshot_for_retry(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990131)
            self.assertTrue(self._save_without_guard_backup())
            with state_module.use_identity(990131):
                state_module.state["tower_retry_count"] = 3

            with patch.object(persistence, "upsert_identity_to_db", side_effect=RuntimeError("injected write failure")), \
                    patch.object(persistence, "_write_live_guard_backup"):
                self.assertFalse(persistence.save_state())

            original = persistence.upsert_identity_to_db
            calls = []

            def record(identity_id):
                calls.append(int(identity_id))
                return original(identity_id)

            with patch.object(persistence, "upsert_identity_to_db", side_effect=record):
                self.assertTrue(self._save_without_guard_backup())

        self.assertEqual([990131], calls)

    def test_reopened_connection_falls_back_to_full_identity_save(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990141)
            state_module.ensure_identity_registered(990142)
            self.assertTrue(self._save_without_guard_backup())
            persistence.get_db_conn().close()
            persistence._db_conn = None
            persistence._db_initialized = False

            original = persistence.upsert_identity_to_db
            calls = []

            def record(identity_id):
                calls.append(int(identity_id))
                return original(identity_id)

            with patch.object(persistence, "upsert_identity_to_db", side_effect=record):
                self.assertTrue(self._save_without_guard_backup())

        self.assertEqual([990141, 990142], calls)

    def test_identity_deletion_removes_rows_and_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990151)
            state_module.ensure_identity_registered(990152)
            self.assertTrue(self._save_without_guard_backup())
            state_module.remove_identity(990151)
            self.assertTrue(self._save_without_guard_backup())
            conn = persistence.get_db_conn()
            table_counts = {
                table_name: conn.execute(
                    f"SELECT COUNT(*) FROM {table_name} WHERE send_as_id = ?",
                    (990151,),
                ).fetchone()[0]
                for table_name in (
                    "identities",
                    "identity_module_state",
                    "identity_timers",
                    "identity_runtime_state",
                )
            }

        self.assertEqual({name: 0 for name in table_counts}, table_counts)
        self.assertNotIn(990151, persistence._persisted_identity_snapshots)

    def test_empty_child_state_deletes_persisted_pending_and_message_rows(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990161)
            with state_module.use_identity(990161):
                state_module.state["pending_tasks"] = {
                    501: {"cmd": ".测试", "sent_at": 100.0, "timeout": 120.0},
                }
                state_module.state["my_msg_ids"] = {501: 100.0}
            self.assertTrue(self._save_without_guard_backup())
            with state_module.use_identity(990161):
                state_module.state["pending_tasks"] = {}
                state_module.state["my_msg_ids"] = {}
            self.assertTrue(self._save_without_guard_backup())
            conn = persistence.get_db_conn()
            pending_count = conn.execute(
                "SELECT COUNT(*) FROM pending_tasks WHERE send_as_id = ?",
                (990161,),
            ).fetchone()[0]
            message_count = conn.execute(
                "SELECT COUNT(*) FROM message_index WHERE send_as_id = ?",
                (990161,),
            ).fetchone()[0]

        self.assertEqual(0, pending_count)
        self.assertEqual(0, message_count)

    def test_meta_and_identity_changes_commit_together(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990171)
            self.assertTrue(self._save_without_guard_backup())
            state_module.set_global_recovery_throttle_until(1_800_000_100.0)
            with state_module.use_identity(990171):
                state_module.state["tower_retry_count"] = 4
            self.assertTrue(self._save_without_guard_backup())
            conn = persistence.get_db_conn()
            meta_value = conn.execute(
                "SELECT value FROM meta WHERE key = ?",
                ("global_recovery_throttle_until",),
            ).fetchone()["value"]
            identity_value = conn.execute(
                "SELECT tower_retry_count FROM identity_runtime_state WHERE send_as_id = ?",
                (990171,),
            ).fetchone()["tower_retry_count"]

        self.assertEqual("1800000100.0", meta_value)
        self.assertEqual(4, int(identity_value))

    def test_snapshot_refresh_failure_rewrites_committed_change_on_next_save(self):
        with tempfile.TemporaryDirectory() as tmpdir, patch.object(
            persistence, "DB_FILE", str(Path(tmpdir) / "state.db")
        ):
            state_module.ensure_identity_registered(990181)
            self.assertTrue(self._save_without_guard_backup())
            with state_module.use_identity(990181):
                state_module.state["tower_retry_count"] = 5

            with patch.object(persistence, "_record_persistence_snapshots", side_effect=RuntimeError("snapshot refresh failed")), \
                    patch.object(persistence, "_write_live_guard_backup"):
                self.assertFalse(persistence.save_state())

            original = persistence.upsert_identity_to_db
            calls = []

            def record(identity_id):
                calls.append(int(identity_id))
                return original(identity_id)

            with patch.object(persistence, "upsert_identity_to_db", side_effect=record):
                self.assertTrue(self._save_without_guard_backup())

        self.assertEqual([990181], calls)


if __name__ == "__main__":
    unittest.main()
