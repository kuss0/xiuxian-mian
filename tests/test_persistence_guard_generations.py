import copy
import json
import shutil
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
            try:
                persistence._db_conn.close()
            except Exception:
                pass
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

    @staticmethod
    def _register_live_roster():
        for identity_id in range(990200, 990210):
            state_module.ensure_identity_registered(identity_id)

    @staticmethod
    def _guard_patches(tmpdir, *, interval=1800):
        guard_dir = Path(tmpdir) / "guard"
        return patch.multiple(
            persistence,
            DB_FILE=str(Path(tmpdir) / "state.db"),
            LIVE_GUARD_DIR=str(guard_dir),
            LIVE_GUARD_DB_FILE=str(guard_dir / "chaogu_state.last-good.db"),
            LIVE_GUARD_PREVIOUS_DB_FILE=str(guard_dir / "chaogu_state.previous.db"),
            LIVE_GUARD_MANIFEST_FILE=str(guard_dir / "manifest.json"),
            LIVE_GUARD_BACKUP_INTERVAL_SEC=float(interval),
        )

    def test_backup_reason_distinguishes_structure_and_periodic_changes(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir, interval=50):
            guard_file = Path(persistence.LIVE_GUARD_DB_FILE)
            guard_file.parent.mkdir(parents=True, exist_ok=True)
            guard_file.write_bytes(b"guard")
            Path(persistence.LIVE_GUARD_MANIFEST_FILE).write_text(
                json.dumps({"saved_at": 100.0}),
                encoding="utf-8",
            )

            self.assertEqual(
                "roster_changed",
                persistence._live_guard_backup_reason(roster_changed=True, committed_change=True, now=101),
            )
            self.assertEqual(
                "account_structure_changed",
                persistence._live_guard_backup_reason(account_structure_changed=True, committed_change=True, now=101),
            )
            self.assertEqual(
                "",
                persistence._live_guard_backup_reason(committed_change=True, now=149),
            )
            self.assertEqual(
                "periodic",
                persistence._live_guard_backup_reason(committed_change=True, now=150),
            )
            self.assertEqual("", persistence._live_guard_backup_reason(committed_change=False, now=999))

    def test_backup_rotation_keeps_previous_generation(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir, interval=0), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True):
            self._register_live_roster()
            self.assertTrue(persistence.save_state())
            with state_module.use_identity(990200):
                state_module.state["tower_retry_count"] = 7
            self.assertTrue(persistence.save_state())

            current_roster = persistence._read_identity_roster_from_db_file(
                persistence.LIVE_GUARD_DB_FILE
            )
            previous_roster = persistence._read_identity_roster_from_db_file(
                persistence.LIVE_GUARD_PREVIOUS_DB_FILE
            )
            manifest = persistence._read_live_guard_manifest()

        self.assertEqual(10, len(current_roster))
        self.assertEqual(10, len(previous_roster))
        self.assertEqual("periodic", manifest["reason"])
        self.assertTrue(manifest["previous_available"])

    def test_no_change_and_recent_ordinary_change_skip_full_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir, interval=3600), \
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
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir, interval=3600), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True):
            self._register_live_roster()
            self.assertTrue(persistence.save_state())
            state_module.set_identity_account(990200, 301299112)

            with patch.object(persistence, "_try_write_live_guard_backup", return_value=True) as backup_mock:
                self.assertTrue(persistence.save_state())

        backup_mock.assert_called_once()
        self.assertEqual("account_structure_changed", backup_mock.call_args.kwargs["reason"])

    def test_backup_failure_does_not_fail_committed_save(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True), \
                patch.object(persistence, "_write_live_guard_backup", side_effect=RuntimeError("backup failed")):
            self._register_live_roster()
            self.assertTrue(persistence.save_state())
            row_count = persistence.get_db_conn().execute(
                "SELECT COUNT(*) FROM identities"
            ).fetchone()[0]

        self.assertEqual(10, row_count)
        self.assertEqual(10, len(persistence._persisted_identity_snapshots))

    def test_restore_falls_back_to_previous_generation(self):
        with tempfile.TemporaryDirectory() as tmpdir, self._guard_patches(tmpdir), \
                patch.object(persistence, "_identity_collapse_guard_enabled", return_value=True):
            self._register_live_roster()
            self.assertTrue(persistence.save_state())
            shutil.copy2(
                persistence.LIVE_GUARD_DB_FILE,
                persistence.LIVE_GUARD_PREVIOUS_DB_FILE,
            )
            Path(persistence.LIVE_GUARD_DB_FILE).write_bytes(b"corrupt")

            conn = persistence.get_db_conn()
            conn.execute("DELETE FROM identities WHERE send_as_id != ?", (990200,))
            conn.commit()
            conn.close()
            persistence._db_conn = None

            self.assertTrue(persistence._maybe_restore_live_guard_backup())
            restored_roster = persistence._read_identity_roster_from_db_file(
                persistence.DB_FILE
            )

        self.assertEqual(10, len(restored_roster))


if __name__ == "__main__":
    unittest.main()
