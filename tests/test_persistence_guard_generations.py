import copy
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from model import persistence
from model import state as state_module


class PersistenceGuardGenerationTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._db_conn_snapshot = persistence._db_conn
        self._db_initialized_snapshot = persistence._db_initialized
        self._schema_key_snapshot = persistence._schema_columns_ensured_key
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
            try:
                persistence._db_conn.close()
            except Exception:
                pass
        persistence._db_conn = self._db_conn_snapshot
        persistence._db_initialized = self._db_initialized_snapshot
        persistence._schema_columns_ensured_key = self._schema_key_snapshot
        persistence._schema_columns_ensured_version = self._schema_version_snapshot
        persistence._persistence_snapshot_db_key = self._snapshot_db_key
        persistence._persisted_meta_snapshot.clear()
        persistence._persisted_meta_snapshot.update(self._meta_snapshot)
        persistence._persisted_identity_snapshots.clear()
        persistence._persisted_identity_snapshots.update(self._identity_snapshots)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    @staticmethod
    def _register_live_roster():
        for identity_id in range(990200, 990210):
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username=f"live{identity_id}")

    @staticmethod
    def _guard_patches(tmpdir, *, refresh_sec=6 * 3600):
        guard_dir = Path(tmpdir) / "guard"
        return patch.multiple(
            persistence,
            DB_FILE=str(Path(tmpdir) / "state.db"),
            LIVE_GUARD_DIR=str(guard_dir),
            LIVE_GUARD_DB_FILE=str(guard_dir / "chaogu_state.last-good.db"),
            LIVE_GUARD_PREVIOUS_DB_FILE=str(guard_dir / "chaogu_state.previous.db"),
            LIVE_GUARD_MANIFEST_FILE=str(guard_dir / "manifest.json"),
            LIVE_GUARD_REFRESH_SEC=float(refresh_sec),
        )

    @staticmethod
    def _runtime_value(db_file, identity_id, column):
        with sqlite3.connect(db_file) as conn:
            row = conn.execute(
                f"SELECT {column} FROM identity_runtime_state WHERE send_as_id = ?",
                (identity_id,),
            ).fetchone()
        return row[0]

    def test_no_change_and_recent_ordinary_change_skip_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True):
            self._register_live_roster()
            self.assertTrue(persistence.save_state())

            with patch.object(persistence, "_try_write_live_guard_backup") as backup_mock:
                self.assertTrue(persistence.save_state())
                backup_mock.assert_not_called()

            with state_module.use_identity(990200):
                state_module.state["tower_retry_count"] = 8
            with patch.object(persistence, "_try_write_live_guard_backup") as backup_mock:
                self.assertTrue(persistence.save_state())
                backup_mock.assert_not_called()

    def test_account_mapping_change_forces_immediate_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True):
            self._register_live_roster()
            self.assertTrue(persistence.save_state())
            state_module.set_identity_account(990200, 301299112)

            with patch.object(
                persistence,
                "_try_write_live_guard_backup",
                return_value=True,
            ) as backup_mock:
                self.assertTrue(persistence.save_state())

        backup_mock.assert_called_once()
        self.assertEqual("account_structure_changed", backup_mock.call_args.kwargs["reason"])

    def test_backup_rotation_keeps_previous_generation(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir, refresh_sec=0), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True):
            self._register_live_roster()
            self.assertTrue(persistence.save_state())
            with state_module.use_identity(990200):
                state_module.state["tower_retry_count"] = 7
            self.assertTrue(persistence.save_state())

            current_value = self._runtime_value(
                persistence.LIVE_GUARD_DB_FILE,
                990200,
                "tower_retry_count",
            )
            previous_value = self._runtime_value(
                persistence.LIVE_GUARD_PREVIOUS_DB_FILE,
                990200,
                "tower_retry_count",
            )
            manifest = persistence._read_live_guard_manifest()

        self.assertEqual(7, current_value)
        self.assertEqual(0, previous_value)
        self.assertEqual(2, manifest["schema"])
        self.assertEqual("periodic", manifest["reason"])
        self.assertTrue(manifest["previous_available"])
        self.assertEqual("bootstrap", manifest["previous_reason"])

    def test_backup_failure_does_not_roll_back_committed_state(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True), \
                patch.object(
                    persistence,
                    "_write_live_guard_backup",
                    side_effect=RuntimeError("backup failed"),
                ), \
                patch.object(persistence.traceback, "print_exc"):
            self._register_live_roster()
            self.assertTrue(persistence.save_state())
            row_count = persistence.get_db_conn().execute(
                "SELECT COUNT(*) FROM identities"
            ).fetchone()[0]

        self.assertEqual(10, row_count)
        self.assertEqual(10, len(persistence._persisted_identity_snapshots))

    def test_rotation_replace_failure_restores_current_generation(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir, refresh_sec=0), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True):
            self._register_live_roster()
            self.assertTrue(persistence.save_state())
            original_manifest = persistence._read_live_guard_manifest()
            with state_module.use_identity(990200):
                state_module.state["tower_retry_count"] = 11

            real_replace = os.replace

            def fail_new_generation(source, target):
                if (
                    str(source) == persistence.LIVE_GUARD_DB_FILE + ".next"
                    and str(target) == persistence.LIVE_GUARD_DB_FILE
                ):
                    raise OSError("replace failed")
                return real_replace(source, target)

            with patch.object(persistence.os, "replace", side_effect=fail_new_generation), \
                    patch.object(persistence.traceback, "print_exc"):
                self.assertTrue(persistence.save_state())

            guard_value = self._runtime_value(
                persistence.LIVE_GUARD_DB_FILE,
                990200,
                "tower_retry_count",
            )
            live_value = self._runtime_value(
                persistence.DB_FILE,
                990200,
                "tower_retry_count",
            )
            manifest = persistence._read_live_guard_manifest()

        self.assertEqual(0, guard_value)
        self.assertEqual(11, live_value)
        self.assertEqual(original_manifest, manifest)

    def test_restore_falls_back_to_previous_and_removes_stale_sidecars(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir, refresh_sec=0), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True):
            self._register_live_roster()
            self.assertTrue(persistence.save_state())
            with state_module.use_identity(990200):
                state_module.state["tower_retry_count"] = 9
            self.assertTrue(persistence.save_state())
            Path(persistence.LIVE_GUARD_DB_FILE).write_bytes(b"corrupt-current")

            conn = persistence.get_db_conn()
            conn.execute("DELETE FROM identities WHERE send_as_id != ?", (990200,))
            conn.commit()
            conn.close()
            persistence._db_conn = None

            real_select = persistence._select_live_guard_restore_file

            def select_after_stale_sidecars():
                Path(persistence.DB_FILE + "-wal").write_bytes(b"stale-wal")
                Path(persistence.DB_FILE + "-shm").write_bytes(b"stale-shm")
                return real_select()

            with patch.object(
                persistence,
                "_select_live_guard_restore_file",
                side_effect=select_after_stale_sidecars,
            ):
                self.assertTrue(persistence._maybe_restore_live_guard_backup())

            restored_roster = persistence._validate_live_guard_db_file(persistence.DB_FILE)
            restored_retry_count = self._runtime_value(
                persistence.DB_FILE,
                990200,
                "tower_retry_count",
            )
            archived = list(Path(tmpdir).glob("state.db.suspicious-*"))
            target_wal_exists = Path(persistence.DB_FILE + "-wal").exists()
            target_shm_exists = Path(persistence.DB_FILE + "-shm").exists()

        self.assertEqual(10, len(restored_roster))
        self.assertEqual(0, restored_retry_count)
        self.assertFalse(target_wal_exists)
        self.assertFalse(target_shm_exists)
        self.assertTrue(any(path.name.endswith("-wal") for path in archived))
        self.assertTrue(any(path.name.endswith("-shm") for path in archived))

    def test_restore_refuses_generations_that_fail_quick_check(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True):
            Path(persistence.LIVE_GUARD_DB_FILE).parent.mkdir(parents=True, exist_ok=True)
            Path(persistence.LIVE_GUARD_DB_FILE).write_bytes(b"bad-current")
            Path(persistence.LIVE_GUARD_PREVIOUS_DB_FILE).write_bytes(b"bad-previous")
            self._register_live_roster()
            self.assertTrue(persistence.save_state())
            conn = persistence.get_db_conn()
            conn.execute("DELETE FROM identities WHERE send_as_id != ?", (990200,))
            conn.commit()
            conn.close()
            persistence._db_conn = None
            Path(persistence.LIVE_GUARD_DB_FILE).write_bytes(b"bad-current-again")
            Path(persistence.LIVE_GUARD_PREVIOUS_DB_FILE).write_bytes(b"bad-previous-again")

            self.assertFalse(persistence._maybe_restore_live_guard_backup())

    def test_corrupt_main_db_restores_from_valid_generation(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True):
            self._register_live_roster()
            self.assertTrue(persistence.save_state())
            persistence.get_db_conn().close()
            persistence._db_conn = None
            Path(persistence.DB_FILE).write_bytes(b"corrupt-main")

            self.assertTrue(persistence._maybe_restore_live_guard_backup())
            restored_roster = persistence._validate_live_guard_db_file(persistence.DB_FILE)

        self.assertEqual(10, len(restored_roster))

    def test_valid_empty_identity_table_restores_from_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True):
            self._register_live_roster()
            self.assertTrue(persistence.save_state())
            conn = persistence.get_db_conn()
            conn.execute("DELETE FROM identities")
            conn.commit()
            conn.close()
            persistence._db_conn = None

            self.assertTrue(persistence._maybe_restore_live_guard_backup())
            restored_roster = persistence._validate_live_guard_db_file(persistence.DB_FILE)

        self.assertEqual(10, len(restored_roster))

    def test_missing_main_db_does_not_restore_stale_guard(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True):
            self._register_live_roster()
            self.assertTrue(persistence.save_state())
            persistence.get_db_conn().close()
            persistence._db_conn = None
            persistence._remove_sqlite_artifacts(persistence.DB_FILE)

            self.assertFalse(persistence._maybe_restore_live_guard_backup())


if __name__ == "__main__":
    unittest.main()
