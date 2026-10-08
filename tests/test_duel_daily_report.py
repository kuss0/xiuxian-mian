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
        "sender_id": 9001,
        "sender_is_bot": True,
        "forwarded": False,
        "text": (
            "【天道战报·文字版】\n"
            f"攻方：@{attacker} · 元婴后期\n"
            f"守方：@{target} · 化神后期大圆满\n"
            f"胜者：@{target} | 净得修为 +{loss}万\n"
            f"败者：@{attacker} | 损失修为 -{loss}万"
        ),
    }


class DuelDailyReportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name, value in (("get_game_bot_ids", [9001]), ("get_game_group_ids", [-1001, -1002])):
            patcher = patch.object(duel_daily_report, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _write_log(self, directory, day, entries):
        with open(os.path.join(directory, f"{day}.log"), "w", encoding="utf-8") as handle:
            for entry in entries:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _report(self, entries):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_log(tmpdir, "2026-07-11", entries)
            with patch.object(duel_daily_report, "_identity_username_map", return_value={
                "growrdick": {"identity_id": 1, "name": "fixture"},
            }):
                return duel_daily_report.build_duel_daily_report("2026-07-11", messages_dir=tmpdir)

    def test_untrusted_copied_and_forwarded_reports_are_not_counted(self):
        for changes in (
            {"sender_id": 22, "sender_is_bot": False},
            {"sender_id": 22, "sender_is_bot": True},
            {"sender_id": 22, "sender_is_bot": True, "sender_username": "hantianzun33_bot"},
            {"sender_id": None}, {"sender_id": "9001"}, {"sender_id": True},
            {"sender_is_bot": False}, {"sender_is_bot": None},
            {"forwarded": True}, {"forwarded": "false"}, {"forwarded": None},
            {"chat_id": -1003}, {"chat_id": "-1001"}, {"event_type": "sent"},
        ):
            with self.subTest(changes=changes):
                report = self._report([_battle(1, "growrdick"), {**_battle(2, "growrdick"), **changes}])
                self.assertEqual((1, 60_000), (report["total_count"], report["total_amount"]))

    def test_latest_edit_and_identical_ids_in_distinct_groups_remain_distinct(self):
        report = self._report([
            _battle(1, "growrdick"),
            {**_battle(1, "growrdick", loss="7.0"), "event_type": "edit"},
            {**_battle(1, "growrdick"), "chat_id": -1002},
        ])
        self.assertEqual((2, 130_000), (report["total_count"], report["total_amount"]))

    def test_non_report_edit_does_not_restore_an_old_result(self):
        report = self._report([
            _battle(1, "growrdick"),
            {**_battle(1, "growrdick"), "event_type": "edit", "text": "result withdrawn"},
        ])
        self.assertEqual(0, report["total_count"])

    def test_old_logs_without_optional_flags_still_require_configured_bot(self):
        trusted = _battle(1, "growrdick")
        trusted.pop("forwarded")
        trusted.pop("sender_is_bot")
        unknown = {**trusted, "message_id": 2}
        unknown.pop("sender_id")
        self.assertEqual(1, self._report([trusted, unknown])["total_count"])

    def test_malformed_log_rows_do_not_abort_other_confirmed_results(self):
        for bad in (None, [], 7, "invalid", {"message_id": "bad", "chat_id": -1001},
                    {**_battle(2, "growrdick"), "message_id": True},
                    {**_battle(2, "growrdick"), "message_id": 0},
                    {**_battle(2, "growrdick"), "event_type": []},
                    {**_battle(2, "growrdick"), "event_type": {}},
                    _battle(2, "growrdick", loss="6.0.0"),
                    _battle(2, "growrdick", loss="9" * 400)):
            with self.subTest(bad=bad):
                report = self._report([_battle(1, "growrdick"), bad])
                self.assertEqual((1, 60_000), (report["total_count"], report["total_amount"]))

    def test_invalid_winner_amount_does_not_invent_zero_gain(self):
        battle = _battle(1, "growrdick")
        battle["text"] = battle["text"].replace("+6.0万", "+6.0.0万")
        report = self._report([battle])
        self.assertEqual((1, 60_000), (report["total_count"], report["total_amount"]))
        self.assertEqual({}, report["target_gains"])

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
