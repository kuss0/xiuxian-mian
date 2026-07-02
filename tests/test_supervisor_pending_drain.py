import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import xiuxian


class SupervisorPendingDrainTests(unittest.TestCase):
    def _db_path(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        return Path(tmpdir.name) / "state.db"

    def test_hot_reload_is_explicit_opt_in(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(xiuxian._hot_reload_enabled())
        with patch.dict(os.environ, {"XIUXIAN_HOT_RELOAD": "1"}):
            self.assertTrue(xiuxian._hot_reload_enabled())
        with patch.dict(os.environ, {"XIUXIAN_HOT_RELOAD": "0"}):
            self.assertFalse(xiuxian._hot_reload_enabled())

    def test_active_pending_windows_reads_generic_pending_tasks(self):
        db_path = self._db_path()
        now = 1_780_500_000.0
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE pending_tasks(
                    msg_id INTEGER PRIMARY KEY,
                    send_as_id INTEGER NOT NULL,
                    cmd TEXT NOT NULL,
                    sent_at REAL NOT NULL,
                    retry INTEGER NOT NULL,
                    timeout REAL NOT NULL,
                    reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                    max_retry INTEGER NOT NULL DEFAULT 3,
                    priority TEXT NOT NULL DEFAULT '',
                    source_module TEXT NOT NULL DEFAULT '',
                    op_id TEXT NOT NULL DEFAULT '',
                    chain_id TEXT NOT NULL DEFAULT '',
                    delete_policy TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                INSERT INTO pending_tasks(msg_id, send_as_id, cmd, sent_at, retry, timeout, source_module)
                VALUES(101, 42, '.探寻裂缝', ?, 0, 60, '探寻裂缝')
                """,
                (now - 30,),
            )
            conn.execute(
                """
                INSERT INTO pending_tasks(msg_id, send_as_id, cmd, sent_at, retry, timeout, source_module)
                VALUES(102, 43, '.过期', ?, 0, 10, '过期')
                """,
                (now - 120,),
            )

        with patch.dict(os.environ, {"XIUXIAN_DB_FILE": str(db_path)}):
            windows = xiuxian._active_pending_windows(now)

        self.assertEqual([101], [item["msg_id"] for item in windows])
        self.assertEqual("探寻裂缝", windows[0]["module"])

    def test_active_pending_windows_reads_runtime_reply_due_fields(self):
        db_path = self._db_path()
        now = 1_780_500_000.0
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    explore_rift_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                    explore_rift_reply_due_at REAL NOT NULL DEFAULT 0,
                    wild_training_reply_to_msg_id INTEGER NOT NULL DEFAULT 0,
                    wild_training_reply_due_at REAL NOT NULL DEFAULT 0,
                    tianxing_observation TEXT NOT NULL DEFAULT '{}',
                    hehuan_observation TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                """
                INSERT INTO identity_runtime_state(
                    send_as_id,
                    explore_rift_reply_to_msg_id,
                    explore_rift_reply_due_at,
                    wild_training_reply_to_msg_id,
                    wild_training_reply_due_at
                )
                VALUES(42, 201, ?, 202, ?)
                """,
                (now + 60, now - 60),
            )

        with patch.dict(os.environ, {"XIUXIAN_DB_FILE": str(db_path)}):
            windows = xiuxian._active_pending_windows(now)

        self.assertEqual([201], [item["msg_id"] for item in windows])
        self.assertEqual("探寻裂缝", windows[0]["module"])

    def test_active_pending_windows_reads_json_pending_fields(self):
        db_path = self._db_path()
        now = 1_780_500_000.0
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """
                CREATE TABLE identity_runtime_state(
                    send_as_id INTEGER PRIMARY KEY,
                    tianxing_observation TEXT NOT NULL DEFAULT '{}',
                    hehuan_observation TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                "INSERT INTO identity_runtime_state(send_as_id, tianxing_observation) VALUES(42, ?)",
                (
                    json.dumps(
                        {
                            "auto_pending_action": "predict",
                            "auto_pending_msg_id": 301,
                            "auto_pending_due_at": now + 90,
                        }
                    ),
                ),
            )

        with patch.dict(os.environ, {"XIUXIAN_DB_FILE": str(db_path)}):
            windows = xiuxian._active_pending_windows(now)

        self.assertEqual([301], [item["msg_id"] for item in windows])
        self.assertEqual("天星:predict", windows[0]["module"])


if __name__ == "__main__":
    unittest.main()
