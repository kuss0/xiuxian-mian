import json
import tempfile
import unittest
from pathlib import Path

from tools import legacy_route_report as report


class LegacyRouteReportTests(unittest.TestCase):
    def test_build_report_matches_direct_sends_and_persisted_sent_events(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            model_dir = root / "model"
            messages_dir = root / "data" / "messages"
            model_dir.mkdir(parents=True)
            messages_dir.mkdir(parents=True)
            (model_dir / "config.py").write_text(
                'CMD_FISHING_LIFT = ".提竿"\nCMD_WILD_TRAINING = ".野外历练"\n',
                encoding="utf-8",
            )
            (model_dir / "sample.py").write_text(
                "async def run(send_game_command, command):\n"
                "    await send_game_command(CMD_FISHING_LIFT)\n"
                "    await send_game_command('.野外历练 深入')\n"
                "    await send_game_command(command)\n",
                encoding="utf-8",
            )
            rows = [
                {"event_type": "sent", "ts": "2026-07-28 01:00:00 UTC+8", "message_id": 1, "sender_id": 9, "text": ".提竿"},
                {"event_type": "message", "ts": "2026-07-28 01:01:00 UTC+8", "message_id": 2, "sender_id": 9, "text": ".提竿"},
                {"event_type": "sent", "ts": "2026-07-28 01:02:00 UTC+8", "message_id": 3, "sender_id": 9, "text": ".野外历练 深入"},
            ]
            (messages_dir / "2026-07-28.log").write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )

            result = report.build_report(root, day="2026-07-28", days=1)

        fishing = result["routes"]["fishing_text_followup"]
        wild = result["routes"]["wild_training_text_run"]
        self.assertEqual(1, len(fishing["direct_send_calls"]))
        self.assertEqual(1, len(fishing["sent_events"]))
        self.assertEqual({"2026-07-28": 1}, fishing["sent_summary"]["by_day"])
        self.assertEqual(1, len(wild["direct_send_calls"]))
        self.assertEqual(1, len(wild["sent_events"]))
        self.assertEqual(1, len(result["unresolved_send_calls"]))

    def test_command_matching_requires_a_command_boundary(self):
        self.assertTrue(report._command_matches(".洞府", ".洞府"))
        self.assertTrue(report._command_matches(".洞府 进入", ".洞府"))
        self.assertFalse(report._command_matches(".洞府入口", ".洞府"))

    def test_fstring_resolution_uses_known_command_constants(self):
        tree = report.ast.parse('f"{CMD_FISHING_OPEN} 银须灵鲢"')
        command = report._resolve_command_expr(tree.body[0].value, {"CMD_FISHING_OPEN": ".开鱼"})
        self.assertEqual(".开鱼 银须灵鲢", command)


if __name__ == "__main__":
    unittest.main()
