import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model.message_log_recovery import recover_sent_command_from_message_log


def _write_log(base_dir, day, entries):
    path = Path(base_dir) / f"{day}.log"
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


class MessageLogRecoveryTests(unittest.TestCase):
    def test_recovers_strict_topic_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_log(tmp, "2026-07-05", [
                {
                    "ts": "2026-07-05 08:24:13 UTC+8",
                    "event_type": "message",
                    "message_id": 11478378,
                    "chat_id": -1001680975844,
                    "sender_id": 7538826434,
                    "reply_to_msg_id": 7310786,
                    "text": ".元婴状态",
                },
            ])

            recovered = recover_sent_command_from_message_log(
                ".元婴状态",
                7538826434,
                1783211058.0,
                start_ts=1783211040.0,
                game_group_id=-1001680975844,
                topic_id=7310786,
                messages_dir=tmp,
            )

        self.assertEqual(11478378, recovered["message_id"])

    def test_recovers_when_topic_anchor_is_logged_differently(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_log(tmp, "2026-07-05", [
                {
                    "ts": "2026-07-05 08:24:13 UTC+8",
                    "event_type": "message",
                    "message_id": 11478378,
                    "chat_id": -1001680975844,
                    "sender_id": 7538826434,
                    "reply_to_msg_id": 999999,
                    "text": ".元婴状态",
                },
            ])

            recovered = recover_sent_command_from_message_log(
                ".元婴状态",
                7538826434,
                1783211058.0,
                start_ts=1783211040.0,
                game_group_id=-1001680975844,
                topic_id=7310786,
                messages_dir=tmp,
            )

        self.assertEqual(11478378, recovered["message_id"])

    def test_recovers_fullwidth_dot_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_log(tmp, "2026-07-05", [
                {
                    "ts": "2026-07-05 08:24:13 UTC+8",
                    "event_type": "message",
                    "message_id": 11478378,
                    "chat_id": -1001680975844,
                    "sender_id": 7538826434,
                    "reply_to_msg_id": 7310786,
                    "text": "。元婴状态",
                },
            ])

            recovered = recover_sent_command_from_message_log(
                ".元婴状态",
                7538826434,
                1783211058.0,
                start_ts=1783211040.0,
                game_group_id=-1001680975844,
                topic_id=7310786,
                messages_dir=tmp,
            )

        self.assertEqual(11478378, recovered["message_id"])

    def test_does_not_recover_other_identity_same_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_log(tmp, "2026-07-05", [
                {
                    "ts": "2026-07-05 08:24:13 UTC+8",
                    "event_type": "message",
                    "message_id": 11478378,
                    "chat_id": -1001680975844,
                    "sender_id": 301299112,
                    "reply_to_msg_id": 7310786,
                    "text": ".元婴状态",
                },
            ])

            recovered = recover_sent_command_from_message_log(
                ".元婴状态",
                7538826434,
                1783211058.0,
                start_ts=1783211040.0,
                game_group_id=-1001680975844,
                topic_id=7310786,
                messages_dir=tmp,
            )

        self.assertIsNone(recovered)


if __name__ == "__main__":
    unittest.main()
