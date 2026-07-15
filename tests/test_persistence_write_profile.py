import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools import persistence_write_profile


class PersistenceWriteProfileTests(unittest.TestCase):
    def test_profile_reads_isolated_db_and_counts_static_save_surface(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_root = root / "model"
            source_root.mkdir()
            persistence_path = source_root / "persistence.py"
            persistence_path.write_text(
                "_META_STATE_CODEC = {'a': object(), 'b': object()}\n"
                "def run():\n"
                "    save_state()\n"
                "    mark_dirty()\n",
                encoding="utf-8",
            )
            (source_root / "feature.py").write_text(
                "def tick():\n"
                "    save_state()\n"
                "    helper.mark_dirty()\n",
                encoding="utf-8",
            )
            db_path = root / "state.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY)")
                conn.execute("CREATE TABLE pending_tasks(msg_id INTEGER PRIMARY KEY)")
                conn.execute("CREATE TABLE message_index(msg_id INTEGER PRIMARY KEY)")
                conn.executemany("INSERT INTO identities(send_as_id) VALUES (?)", [(1,), (2,)])
                conn.execute("INSERT INTO pending_tasks(msg_id) VALUES (10)")
                conn.executemany("INSERT INTO message_index(msg_id) VALUES (?)", [(20,), (21,), (22,)])
                conn.commit()
            guard_path = root / "guard.db"
            guard_path.write_bytes(b"guard")

            payload = persistence_write_profile.build_profile(
                db_path,
                source_root=source_root,
                persistence_path=persistence_path,
                guard_path=guard_path,
            )

        self.assertEqual(2, payload["identity_count"])
        self.assertEqual(2, payload["meta_codec_key_count"])
        self.assertEqual(18, payload["minimum_mutating_statements_per_full_save"])
        self.assertEqual({"save_state": 2, "mark_dirty": 2}, payload["source_call_counts"])
        self.assertEqual(5, payload["guard_backup"]["size_bytes"])
        self.assertGreater(payload["db_size_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
