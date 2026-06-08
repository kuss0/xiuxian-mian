import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tools import analyze_game_records


class AnalyzeGameRecordsTests(unittest.TestCase):
    def test_command_key_preserves_multi_token_commands(self):
        self.assertEqual(".神迹 布道", analyze_game_records.command_key(".神迹 布道"))
        self.assertEqual(".神迹 赈灾", analyze_game_records.command_key(".神迹 赈灾"))
        self.assertEqual(".交换 法宝", analyze_game_records.command_key(".交换 法宝"))
        self.assertEqual(".加入苍坤洞府", analyze_game_records.command_key(".加入苍坤洞府 123"))
        self.assertEqual(".加入昆吾山", analyze_game_records.command_key(".加入昆吾山 456"))
        self.assertEqual(".加入落云秘圃", analyze_game_records.command_key(".加入落云秘圃 12"))
        self.assertEqual("replica", analyze_game_records.command_family(".加入苍坤洞府"))
        self.assertEqual("replica", analyze_game_records.command_family(".加入昆吾山"))
        self.assertEqual("replica", analyze_game_records.command_family(".落云抉择"))
        self.assertEqual("small_world", analyze_game_records.command_family(".神迹 布道"))
        self.assertEqual("small_world", analyze_game_records.command_family(".神迹 赈灾"))
        self.assertEqual("explore_rift", analyze_game_records.command_family(".探寻裂缝"))
        self.assertEqual("hehuan", analyze_game_records.command_family(".双修"))
        self.assertEqual("replica", analyze_game_records.command_family(".后殿抉择"))
        self.assertEqual("tower", analyze_game_records.command_family(".重置古塔"))

    def test_analyze_jsonl_logs_tracks_sent_reply_and_short_repeat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            messages_dir = Path(tmpdir)
            rows = [
                {
                    "ts": "2026-05-23 10:00:00 UTC+8",
                    "event_type": "sent",
                    "message_id": 11,
                    "chat_id": -1001,
                    "sender_id": 100,
                    "topic_id": 1,
                    "reply_to_msg_id": 0,
                    "text": ".天机代卜",
                },
                {
                    "ts": "2026-05-23 10:00:01 UTC+8",
                    "event_type": "message",
                    "message_id": 12,
                    "chat_id": -1001,
                    "sender_id": 8349385938,
                    "topic_id": 1,
                    "reply_to_msg_id": 11,
                    "text": "天机链路尚未重铸，请在 24 秒后再试。",
                },
                {
                    "ts": "2026-05-23 10:00:20 UTC+8",
                    "event_type": "sent",
                    "message_id": 13,
                    "chat_id": -1001,
                    "sender_id": 100,
                    "topic_id": 1,
                    "reply_to_msg_id": 0,
                    "text": ".天机代卜",
                },
            ]
            (messages_dir / "2026-05-23.log").write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )

            analysis = analyze_game_records.analyze_jsonl_logs(messages_dir)
            health = analyze_game_records.summarize_sent_health(analysis)

        self.assertEqual(3, analysis.scanned_lines)
        self.assertEqual(2, analysis.command_stats[".天机代卜"].sent_count)
        self.assertEqual(1, analysis.bot_reply_categories["cooldown"])
        self.assertEqual(1, len(health["duplicate_short_gap"]))
        self.assertEqual(1, health["missing_direct_replies_total"])

    def test_non_command_sent_is_included_in_send_health(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            messages_dir = Path(tmpdir)
            rows = [
                {
                    "ts": "2026-05-23 10:00:00 UTC+8",
                    "event_type": "sent",
                    "message_id": 21,
                    "chat_id": -1001,
                    "sender_id": 100,
                    "topic_id": 1,
                    "reply_to_msg_id": 0,
                    "text": "1",
                },
                {
                    "ts": "2026-05-23 10:00:01 UTC+8",
                    "event_type": "sent",
                    "message_id": 22,
                    "chat_id": -1001,
                    "sender_id": 100,
                    "topic_id": 1,
                    "reply_to_msg_id": 0,
                    "text": "1",
                },
            ]
            (messages_dir / "2026-05-23.log").write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )

            analysis = analyze_game_records.analyze_jsonl_logs(messages_dir)
            health = analyze_game_records.summarize_sent_health(analysis)

        self.assertEqual(2, health["sent_total"])
        self.assertEqual(2, analysis.sent_by_family["non_command"])
        self.assertEqual(1, len(health["duplicate_short_gap"]))

    def test_log_group_command_stats_are_limited_to_configured_chat(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            messages_dir = Path(tmpdir)
            rows = [
                {
                    "ts": "2026-05-23 10:00:00 UTC+8",
                    "event_type": "message",
                    "message_id": 31,
                    "chat_id": -1001,
                    "sender_id": 100,
                    "text": ".状态",
                },
                {
                    "ts": "2026-05-23 10:00:01 UTC+8",
                    "event_type": "message",
                    "message_id": 32,
                    "chat_id": -2002,
                    "sender_id": 100,
                    "text": ".状态",
                },
            ]
            (messages_dir / "2026-05-23.log").write_text(
                "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                encoding="utf-8",
            )

            analysis = analyze_game_records.analyze_jsonl_logs(messages_dir, log_group_id=-2002)

        self.assertEqual(2, analysis.command_stats[".状态"].count)
        self.assertEqual(1, analysis.log_group_command_stats[".状态"].count)
        self.assertEqual("-2002", analysis.log_group_command_stats[".状态"].chats.most_common(1)[0][0])

    def test_extract_static_inventory_from_small_sources(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            model_dir = root / "model"
            parser_dir = root / "mini" / "backend" / "parsers"
            model_dir.mkdir(parents=True)
            parser_dir.mkdir(parents=True)
            (model_dir / "config.py").write_text(
                "import re\n"
                "CMD_FOO = '.测试'\n"
                "MODULE_NAMES = ['灵树']\n"
                "RE_CMD_STATUS = re.compile(r'^\\.状态$')\n",
                encoding="utf-8",
            )
            (model_dir / "control.py").write_text(
                "def _format_log_group_help_html():\n"
                "    module_commands = ['.状态']\n"
                "    control_commands = ['.全局暂停']\n",
                encoding="utf-8",
            )
            (model_dir / "app_replica.py").write_text("CMD = '.加入苍坤洞府'\n", encoding="utf-8")
            (parser_dir / "__init__.py").write_text(
                "def build_parser_registry():\n"
                "    registry.register(RiskParser())\n",
                encoding="utf-8",
            )

            inventory = analyze_game_records.extract_static_source_inventory(root, root / "mini")

        self.assertEqual(".测试", inventory["cmd_constants"]["CMD_FOO"])
        self.assertEqual(["灵树"], inventory["module_names"])
        self.assertIn(".状态", inventory["log_group_help_commands"])
        self.assertIn(".加入苍坤洞府", inventory["replica_command_literals"])
        self.assertEqual(["RiskParser"], inventory["miniweb_parsers"])


if __name__ == "__main__":
    unittest.main()
