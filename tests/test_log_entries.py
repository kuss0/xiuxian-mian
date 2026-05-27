import atexit
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
CREATED_ENV = False

if not ENV_PATH.exists():
    ENV_PATH.write_text(
        "\n".join(
            [
                "API_ID=12345",
                "API_HASH=00000000000000000000000000000000",
                "TG_PROXY_TYPE=",
                "TG_PROXY_HOST=127.0.0.1:7890",
                "LOG_GROUP_ID=0",
                "LOG_SEND_MODE=account",
                "ADMIN_ID=1",
                "CHAOGU_UI_HOST=127.0.0.1",
                "CHAOGU_UI_PORT=3030",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    CREATED_ENV = True

if CREATED_ENV:
    atexit.register(lambda: ENV_PATH.exists() and ENV_PATH.unlink())

sys.path.insert(0, str(PROJECT_ROOT))

from model import ui


class LogEntryTests(unittest.TestCase):
    def test_split_log_query_terms_deduplicates_casefolded_terms(self):
        self.assertEqual(["小世界", "wa2000"], ui._split_log_query_terms("  小世界  WA2000 wa2000  "))

    def test_list_message_log_days_uses_only_main_group_logs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "2026-05-15.log").write_text("", encoding="utf-8")
            (Path(tmpdir) / "2026-05-16.log").write_text("", encoding="utf-8")
            (Path(tmpdir) / "replica-2026-05-17.log").write_text("", encoding="utf-8")

            with patch.object(ui, "MESSAGES_DIR", tmpdir):
                days = ui._list_message_log_days()

        self.assertEqual(["2026-05-16", "2026-05-15"], days)

    def test_read_log_entries_defaults_to_newest_main_group_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2026-05-16.log"
            rows = [
                {"ts": "10:00", "event_type": "message", "sender_id": 1, "message_id": 11, "text": "旧主群记录"},
                {"ts": "10:01", "event_type": "message", "sender_id": 2, "message_id": 12, "text": "较新主群记录"},
                {"ts": "10:02", "event_type": "sent", "sender_id": 3, "message_id": 13, "text": "最新主群记录"},
            ]
            log_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
            (Path(tmpdir) / "replica-2026-05-16.log").write_text(
                json.dumps({"ts": "10:03", "event_type": "message", "sender_id": 4, "message_id": 99, "text": "副本群记录"}, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

            with patch.object(ui, "MESSAGES_DIR", tmpdir):
                result = ui._read_log_entries("2026-05-16", limit=2)

        self.assertEqual(3, result["total"])
        self.assertTrue(result["has_more"])
        self.assertEqual([13, 12], [entry["message_id"] for entry in result["entries"]])

    def test_read_log_entries_requires_all_query_terms(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2026-05-16.log"
            rows = [
                {"ts": "10:00", "event_type": "message", "sender_id": 1, "message_id": 11, "text": "WA2000 小世界 显灵"},
                {"ts": "10:01", "event_type": "message", "sender_id": 2, "message_id": 12, "text": "WA2000 小世界 等待"},
                {"ts": "10:02", "event_type": "sent", "sender_id": 1, "message_id": 13, "text": "显灵"},
            ]
            log_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

            with patch.object(ui, "MESSAGES_DIR", tmpdir):
                result = ui._read_log_entries("2026-05-16", "wa2000 显灵")

        self.assertEqual(1, result["total"])
        self.assertEqual(11, result["entries"][0]["message_id"])

    def test_read_log_entries_searches_button_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2026-05-16.log"
            rows = [
                {
                    "ts": "10:00",
                    "event_type": "message",
                    "sender_id": 1,
                    "message_id": 11,
                    "text": "天道阵列验证",
                    "buttons": [[{"text": "稳固道心", "type": "callback"}]],
                },
                {
                    "ts": "10:01",
                    "event_type": "message",
                    "sender_id": 2,
                    "message_id": 12,
                    "text": "天道阵列验证",
                    "buttons": [[{"text": "破阵", "type": "callback"}]],
                },
            ]
            log_path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

            with patch.object(ui, "MESSAGES_DIR", tmpdir):
                result = ui._read_log_entries("2026-05-16", "阵列 稳固")

        self.assertEqual(1, result["total"])
        self.assertEqual(11, result["entries"][0]["message_id"])
        self.assertEqual("稳固道心", result["entries"][0]["buttons"][0][0]["text"])


if __name__ == "__main__":
    unittest.main()
