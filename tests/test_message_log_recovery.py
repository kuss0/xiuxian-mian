import json
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model.message_log_recovery import (
    find_message_log_message,
    find_message_log_replies,
    find_recent_message_log_command,
    find_message_log_replies_tail,
    parse_message_log_ts,
    recover_sent_command_from_message_log,
)


def _write_log(base_dir, day, entries):
    path = Path(base_dir) / f"{day}.log"
    with path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


class MessageLogRecoveryTests(unittest.TestCase):
    def test_structured_lookups_filter_cross_chat_id_collisions(self):
        now = parse_message_log_ts("2026-07-15 10:55:11 UTC+8")
        game_group_id = -1001680975844
        with tempfile.TemporaryDirectory() as tmp:
            _write_log(tmp, "2026-07-15", [
                {
                    "ts": "2026-07-15 10:55:05 UTC+8",
                    "event_type": "sent",
                    "message_id": 154926,
                    "chat_id": -1009999999999,
                    "sender_id": 7538826434,
                    "text": ".宗门点卯",
                },
                {
                    "ts": "2026-07-15 10:55:06 UTC+8",
                    "event_type": "sent",
                    "message_id": 154926,
                    "chat_id": game_group_id,
                    "sender_id": 7538826434,
                    "text": ".宗门点卯",
                },
                {
                    "ts": "2026-07-15 10:55:07 UTC+8",
                    "event_type": "message",
                    "message_id": 154927,
                    "chat_id": -1009999999999,
                    "reply_to_msg_id": 154926,
                    "text": "其他群回复",
                },
                {
                    "ts": "2026-07-15 10:55:08 UTC+8",
                    "event_type": "message",
                    "message_id": 154927,
                    "chat_id": game_group_id,
                    "reply_to_msg_id": 154926,
                    "text": "点卯成功",
                },
            ])

            replies = find_message_log_replies(154926, now, chat_id=game_group_id, messages_dir=tmp)
            message = find_message_log_message(154927, now, chat_id=game_group_id, messages_dir=tmp)
            command = find_recent_message_log_command(
                now,
                sender_id=7538826434,
                chat_id=game_group_id,
                command_predicate=lambda entry: entry.get("text") == ".宗门点卯",
                messages_dir=tmp,
            )

        self.assertEqual(["点卯成功"], [item["text"] for item in replies])
        self.assertEqual("点卯成功", message["text"])
        self.assertEqual(game_group_id, command["chat_id"])

    def test_tail_reply_lookup_finds_reply_before_late_sent_row(self):
        now = parse_message_log_ts("2026-07-15 10:55:11 UTC+8")
        with tempfile.TemporaryDirectory() as tmp:
            _write_log(tmp, "2026-07-15", [
                {
                    "ts": "2026-07-15 10:55:08 UTC+8",
                    "event_type": "message",
                    "message_id": 154927,
                    "chat_id": -1001680975844,
                    "sender_id": 8861328042,
                    "reply_to_msg_id": 154926,
                    "text": "点卯成功！你获得了 105 点宗门贡献。",
                },
                {
                    "ts": "2026-07-15 10:55:11 UTC+8",
                    "event_type": "sent",
                    "message_id": 154926,
                    "chat_id": -1001680975844,
                    "sender_id": 7538826434,
                    "reply_to_msg_id": 0,
                    "text": ".宗门点卯",
                },
            ])

            replies = find_message_log_replies_tail(
                154926,
                now,
                messages_dir=tmp,
                lookback_sec=30,
            )

        self.assertEqual([154927], [item["message_id"] for item in replies])

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
