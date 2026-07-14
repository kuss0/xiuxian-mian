import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tools import module_latency_report


TZ_LOCAL = timezone(timedelta(hours=8))


class ModuleLatencyReportTests(unittest.TestCase):
    def test_report_correlates_direct_reply_and_final_edit(self):
        now = datetime(2026, 7, 15, 12, 0, 0, tzinfo=TZ_LOCAL)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-07-15.log"
            rows = [
                {
                    "ts": "2026-07-15 10:00:00 UTC+8",
                    "event_type": "sent",
                    "message_id": 100,
                    "chat_id": -1001,
                    "sender_id": 2001,
                    "text": ".野外历练 谨慎",
                    "source_module": "野外历练",
                },
                {
                    "ts": "2026-07-15 10:00:02 UTC+8",
                    "event_type": "message",
                    "message_id": 101,
                    "chat_id": -1001,
                    "reply_to_msg_id": 100,
                    "text": "出发",
                },
                {
                    "ts": "2026-07-15 10:00:07 UTC+8",
                    "event_type": "edit",
                    "message_id": 101,
                    "chat_id": -1001,
                    "reply_to_msg_id": 100,
                    "text": "结果",
                },
                {
                    "ts": "2026-07-15 10:10:00 UTC+8",
                    "event_type": "sent",
                    "message_id": 110,
                    "chat_id": -1001,
                    "sender_id": 2002,
                    "text": ".野外历练 谨慎",
                    "source_module": "野外历练",
                },
            ]
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

            report = module_latency_report.build_latency_report(tmp, since_hours=24, now=now)

        wild = report["modules"]["wild_training"]
        self.assertEqual(2, wild["sent"])
        self.assertEqual(1, wild["replied"])
        self.assertEqual(1, wild["missing"])
        self.assertEqual(2.0, wild["first_reply"]["p99_sec"])
        self.assertEqual(7.0, wild["final_event"]["p99_sec"])
        self.assertEqual(110, wild["missing_samples"][0]["message_id"])

    def test_report_keeps_chat_ids_separate(self):
        now = datetime(2026, 7, 15, 12, 0, 0, tzinfo=TZ_LOCAL)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-07-15.log"
            rows = [
                {
                    "ts": "2026-07-15 11:00:00 UTC+8",
                    "event_type": "sent",
                    "message_id": 100,
                    "chat_id": -1001,
                    "sender_id": 2001,
                    "text": ".斗法 @target",
                    "source_module": "斗法",
                },
                {
                    "ts": "2026-07-15 11:00:01 UTC+8",
                    "event_type": "message",
                    "message_id": 101,
                    "chat_id": -1002,
                    "reply_to_msg_id": 100,
                    "text": "other chat",
                },
            ]
            path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")

            report = module_latency_report.build_latency_report(tmp, since_hours=24, now=now)

        duel = report["modules"]["duel"]
        self.assertEqual(1, duel["sent"])
        self.assertEqual(0, duel["replied"])
        self.assertEqual(1, duel["missing"])


if __name__ == "__main__":
    unittest.main()
