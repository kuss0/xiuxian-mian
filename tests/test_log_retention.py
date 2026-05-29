import os
import tempfile
import unittest
from pathlib import Path

from model import log_retention


class LogRetentionTests(unittest.TestCase):
    def test_cleanup_log_files_deletes_files_older_than_retention(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmpdir:
            old_path = Path(tmpdir) / "2026-05-01.log"
            fresh_path = Path(tmpdir) / "2026-05-02.log"
            old_path.write_text("old\n", encoding="utf-8")
            fresh_path.write_text("fresh\n", encoding="utf-8")
            os.utime(old_path, (now - 3 * 86400, now - 3 * 86400))
            os.utime(fresh_path, (now, now))

            result = log_retention.cleanup_log_files(
                tmpdir,
                suffixes=(".log",),
                retention_days=2,
                now=now,
            )

            self.assertEqual(1, result["deleted"])
            self.assertFalse(old_path.exists())
            self.assertTrue(fresh_path.exists())

    def test_cleanup_log_files_enforces_total_size_but_keeps_newest_file(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmpdir:
            oldest = Path(tmpdir) / "2026-05-01.log"
            middle = Path(tmpdir) / "2026-05-02.log"
            newest = Path(tmpdir) / "2026-05-03.log"
            for index, path in enumerate((oldest, middle, newest)):
                path.write_text("x" * 10, encoding="utf-8")
                ts = now - (3 - index) * 60
                os.utime(path, (ts, ts))

            result = log_retention.cleanup_log_files(
                tmpdir,
                suffixes=(".log",),
                max_bytes=15,
                now=now,
            )

            self.assertEqual(2, result["deleted"])
            self.assertFalse(oldest.exists())
            self.assertFalse(middle.exists())
            self.assertTrue(newest.exists())

    def test_cleanup_log_files_recurses_for_workflow_logs(self):
        now = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_dir = Path(tmpdir) / "deep_retreat"
            nested_dir.mkdir()
            nested = nested_dir / "2026-05-01.jsonl"
            nested.write_text("{}\n", encoding="utf-8")
            os.utime(nested, (now - 3 * 86400, now - 3 * 86400))

            result = log_retention.cleanup_log_files(
                tmpdir,
                suffixes=(".jsonl",),
                retention_days=2,
                recursive=True,
                now=now,
            )

            self.assertEqual(1, result["deleted"])
            self.assertFalse(nested.exists())


if __name__ == "__main__":
    unittest.main()
