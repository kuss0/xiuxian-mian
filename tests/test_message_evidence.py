import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import runtime
from model.features import passive_inbox


class PassiveInboxEvidenceTests(unittest.TestCase):
    def setUp(self):
        self._stats_snapshot = copy.deepcopy(passive_inbox._passive_stats)
        passive_inbox._passive_stats = {
            "total": 0,
            "changed": 0,
            "skipped": 0,
            "modules": {},
            "skip_reasons": {},
            "recent": [],
        }

    def tearDown(self):
        passive_inbox._passive_stats = self._stats_snapshot

    def test_no_reply_context_counts_without_recent_noise(self):
        with patch.object(passive_inbox, "_save_passive_stats"):
            ok = passive_inbox.record_passive_inbox_event(
                "skipped",
                reason="no_reply_context",
                matched_text="【第二元神归位】",
                decision="skip_missing_identity",
                include_recent=False,
            )

        self.assertTrue(ok)
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["total"])
        self.assertEqual(1, snapshot["skipped"])
        self.assertEqual(1, snapshot["skip_reasons"]["no_reply_context"])
        self.assertEqual([], snapshot["recent"])

    def test_structured_recent_fields_are_kept_and_shown(self):
        with patch.object(passive_inbox, "_save_passive_stats"):
            ok = passive_inbox.record_passive_inbox_event(
                "changed",
                module="taiyi",
                identity_id=8659059191,
                family="taiyi_yindao",
                msg_id=9446793,
                reply_to_msg_id=9446793,
                decision="calibrate_manual_late_no_search",
                matched_text="你引动【水之道】，获得了 100点神识！",
                summary="引道手动/迟到成功",
            )

        self.assertTrue(ok)
        event = passive_inbox.get_passive_inbox_snapshot()["recent"][-1]
        self.assertEqual("taiyi_yindao", event["family"])
        self.assertEqual(9446793, event["msg_id"])
        self.assertEqual("calibrate_manual_late_no_search", event["decision"])
        status_text = passive_inbox.get_passive_inbox_status_text()
        self.assertIn("family=taiyi_yindao", status_text)
        self.assertIn("decision=calibrate_manual_late_no_search", status_text)
        self.assertIn("reply=9446793", status_text)


class SentMessageEvidenceTests(unittest.TestCase):
    def test_sent_log_records_family_priority_and_track(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(runtime, "MESSAGES_DIR", tmpdir),
                patch.object(runtime, "get_game_group_id", return_value=-1001680975844),
                patch.object(runtime, "get_game_topic_id", return_value=7310786),
            ):
                runtime._append_sent_message_log(
                    9446793,
                    ".引道 水",
                    8659059191,
                    reply_to_msg_id=0,
                    priority="chain",
                    track=False,
                    intent={
                        "source_module": "太一",
                        "op_id": "taiyi-yindao-9446793",
                        "chain_id": "taiyi-cycle-01",
                        "delete_policy": "manual_keep",
                    },
                )

            log_files = list(Path(tmpdir).glob("*.log"))
            self.assertEqual(1, len(log_files))
            payload = json.loads(log_files[0].read_text(encoding="utf-8").strip())

        self.assertEqual("sent", payload["event_type"])
        self.assertEqual("taiyi_yindao", payload["family"])
        self.assertEqual("chain", payload["priority"])
        self.assertIs(False, payload["track"])
        self.assertEqual("太一", payload["source_module"])
        self.assertEqual("taiyi-yindao-9446793", payload["op_id"])
        self.assertEqual("taiyi-cycle-01", payload["chain_id"])
        self.assertEqual("manual_keep", payload["delete_policy"])

    def test_send_intent_infers_module_and_delete_policy(self):
        with patch.object(runtime, "is_auto_delete_sent_messages_enabled", return_value=True):
            intent = runtime._normalize_send_intent(".引道 水", op_id="op-1")

        self.assertEqual("太一", intent["source_module"])
        self.assertEqual("op-1", intent["op_id"])
        self.assertEqual("auto_delete", intent["delete_policy"])


if __name__ == "__main__":
    unittest.main()
