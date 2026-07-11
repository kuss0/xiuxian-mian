import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model.config import TZ_LOCAL
from model.features import duel_daily_report


def _battle(message_id, attacker, target="ccahen", loss="6.0"):
    return {
        "message_id": message_id,
        "chat_id": -1001,
        "event_type": "message",
        "text": (
            "【天道战报·文字版】\n"
            f"攻方：@{attacker} · 元婴后期\n"
            f"守方：@{target} · 化神后期大圆满\n"
            f"胜者：@{target} | 净得修为 +{loss}万\n"
            f"败者：@{attacker} | 损失修为 -{loss}万"
        ),
    }


class DuelDailyReportTests(unittest.IsolatedAsyncioTestCase):
    def _write_log(self, directory, day, entries):
        with open(os.path.join(directory, f"{day}.log"), "w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def test_build_report_counts_only_real_own_battles_and_dedupes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            entries = [
                _battle(1, "growrdick"),
                _battle(1, "growrdick"),
                _battle(2, "Lpprceqei"),
                {"message_id": 3, "chat_id": -1001, "text": "道友 @ccahen 元神尚未平复，5分钟内无法再次斗法。"},
                _battle(4, "outsider"),
            ]
            self._write_log(tmpdir, "2026-07-11", entries)
            profiles = {
                1: {"username": "growrdick"},
                2: {"username": "Lpprceqei"},
            }
            with (
                patch.object(duel_daily_report, "get_identity_ids", return_value=[1, 2]),
                patch.object(duel_daily_report, "get_send_as_profile", side_effect=lambda identity_id: profiles[identity_id]),
                patch.object(duel_daily_report, "get_identity_display_name", side_effect=lambda identity_id: {1: "丁丁", 2: "Lsfnqy"}[identity_id]),
            ):
                report = duel_daily_report.build_duel_daily_report("2026-07-11", messages_dir=tmpdir)

        self.assertEqual(2, report["total_count"])
        self.assertEqual(120_000, report["total_amount"])
        self.assertEqual({"@ccahen": 120_000}, report["target_gains"])
        self.assertIn("转出修为 12万", duel_daily_report.format_duel_daily_report(report))

    async def test_scheduler_sends_once_at_2355(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            messages_dir = os.path.join(tmpdir, "messages")
            state_dir = os.path.join(tmpdir, "state")
            os.makedirs(messages_dir)
            os.makedirs(state_dir)
            self._write_log(messages_dir, "2026-07-11", [_battle(1, "growrdick")])
            now = datetime(2026, 7, 11, 23, 55, tzinfo=TZ_LOCAL).timestamp()
            send_mock = AsyncMock(return_value=True)
            with (
                patch.object(duel_daily_report, "MESSAGES_DIR", messages_dir),
                patch.object(duel_daily_report, "STATE_FILE", os.path.join(state_dir, "duel_daily_report_state.json")),
                patch.object(duel_daily_report, "get_identity_ids", return_value=[1]),
                patch.object(duel_daily_report, "get_send_as_profile", return_value={"username": "growrdick"}),
                patch.object(duel_daily_report, "get_identity_display_name", return_value="丁丁"),
                patch.object(duel_daily_report, "_report_state_loaded", False),
                patch.object(duel_daily_report, "_report_state", {}),
                patch.object(duel_daily_report, "_last_sent_day_memory", ""),
                patch.object(duel_daily_report, "_next_retry_at", 0.0),
                patch.object(duel_daily_report, "send_audit_log", new=send_mock),
            ):
                self.assertTrue(await duel_daily_report.run_duel_daily_report_scheduler(now))
                self.assertFalse(await duel_daily_report.run_duel_daily_report_scheduler(now + 5))
        send_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
