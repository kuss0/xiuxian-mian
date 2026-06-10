import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import message_contract, module_manifest
from model.features import passive_event_ledger
from tools import message_contract_report


class MessageContractTests(unittest.TestCase):
    def test_record_unhandled_routed_reply_uses_manifest_owner(self):
        event = SimpleNamespace(id=10140775, chat_id=-1001680975844)

        with patch.object(message_contract.passive_inbox, "record_passive_inbox_event", return_value=True) as inbox_mock:
            ok = message_contract.record_unhandled_routed_reply(
                event,
                "【乱星海远航·启】\n预计归航时间：12小时 后",
                3504367852,
                "concubine_voyage",
                10140774,
                event_kind="edit",
                reply_to_sender_id=8325841058,
            )

        self.assertTrue(ok)
        inbox_mock.assert_called_once()
        args, kwargs = inbox_mock.call_args
        self.assertEqual(("skipped",), args)
        self.assertEqual("侍妾远航", kwargs["module"])
        self.assertEqual(3504367852, kwargs["identity_id"])
        self.assertEqual("unhandled_routed_reply", kwargs["reason"])
        self.assertEqual("handler_not_matched", kwargs["decision"])
        self.assertEqual("concubine_voyage", kwargs["family"])
        self.assertEqual(10140775, kwargs["msg_id"])
        self.assertEqual(10140774, kwargs["reply_to_msg_id"])
        self.assertEqual(8325841058, kwargs["reply_to_sender_id"])
        self.assertEqual("edit:reply_context", kwargs["route_source"])
        self.assertTrue(kwargs["include_recent"])

    def test_unhandled_reply_iterator_summary_and_fixture_suggestion(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(passive_event_ledger, "PASSIVE_EVENT_LEDGER_DIR", tmpdir):
                passive_event_ledger.append_passive_event(
                    kind="skipped",
                    module="侍妾远航",
                    identity_id=3504367852,
                    reason="unhandled_routed_reply",
                    family="concubine_voyage",
                    chat_id=-1001680975844,
                    msg_id=10140775,
                    reply_to_msg_id=10140774,
                    event_type="message",
                    route_source="message:reply_context",
                    matched_text="【乱星海远航·启】\n预计归航时间：12小时 后",
                    decision="handler_not_matched",
                    now=1_781_077_200.0,
                )
                passive_event_ledger.append_passive_event(
                    kind="changed",
                    module="太一",
                    identity_id=1,
                    reason="handled",
                    family="taiyi_yindao",
                    msg_id=2,
                    matched_text="ok",
                    decision="done",
                    now=1_781_077_201.0,
                )
                path = passive_event_ledger.get_passive_event_ledger_path(1_781_077_200.0)
                events = list(message_contract.iter_unhandled_routed_replies(path=path, limit=10))

        self.assertEqual(1, len(events))
        summary = message_contract.summarize_unhandled_routed_replies(events)
        self.assertEqual(1, summary["total"])
        self.assertEqual({"侍妾远航": 1}, summary["by_module"])
        self.assertEqual({"concubine_voyage": 1}, summary["by_family"])
        sample_id, payload = message_contract.build_replay_sample_suggestion(events[0], source="ledger:10140775")
        self.assertEqual("contract_gap.concubine_voyage.message.10140775", sample_id)
        self.assertEqual("concubine_voyage", payload["module"])
        self.assertEqual("concubine_voyage", payload["family"])
        self.assertIn("乱星海远航", payload["text"])

    def test_report_tool_json_output_is_read_only_summary(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(passive_event_ledger, "PASSIVE_EVENT_LEDGER_DIR", tmpdir):
                passive_event_ledger.append_passive_event(
                    kind="skipped",
                    module="侍妾远航",
                    identity_id=3504367852,
                    reason="unhandled_routed_reply",
                    family="concubine_voyage",
                    msg_id=10140775,
                    reply_to_msg_id=10140774,
                    event_type="message",
                    route_source="message:reply_context",
                    matched_text="【乱星海远航·归】\n修为 +658",
                    decision="handler_not_matched",
                    now=1_781_077_200.0,
                )
                path = passive_event_ledger.get_passive_event_ledger_path(1_781_077_200.0)

            out = io.StringIO()
            with redirect_stdout(out):
                code = message_contract_report.main([
                    "--ledger-path",
                    path,
                    "--json",
                    "--latest",
                    "1",
                ])

        self.assertEqual(0, code)
        payload = json.loads(out.getvalue())
        self.assertEqual(1, payload["summary"]["total"])
        self.assertEqual({"侍妾远航": 1}, payload["summary"]["by_module"])
        self.assertEqual("contract_gap.concubine_voyage.message.10140775", payload["suggestions"][0]["sample_id"])

    def test_contract_gap_summary_resolves_module_from_family(self):
        event = {
            "kind": "skipped",
            "reason": "reply_context_no_identity",
            "family": "concubine_voyage",
            "msg_id": 10146047,
            "reply_to_sender_id": 8325841058,
            "matched_text": "【乱星海远航·归】",
            "decision": "skip_missing_identity",
        }

        summary = message_contract.summarize_message_contract_gaps([event])
        line = message_contract.format_unhandled_reply_line(event)

        self.assertEqual(1, summary["total"])
        self.assertEqual({"侍妾远航": 1}, summary["by_module"])
        self.assertEqual({"reply_context_no_identity": 1}, summary["by_reason"])
        self.assertIn("侍妾远航/concubine_voyage", line)
        self.assertIn("reason=reply_context_no_identity", line)
        self.assertIn("cmd_sender=8325841058", line)

    def test_status_text_is_log_group_reminder_only(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(passive_event_ledger, "PASSIVE_EVENT_LEDGER_DIR", tmpdir):
                passive_event_ledger.append_passive_event(
                    kind="skipped",
                    reason="reply_context_no_identity",
                    family="concubine_voyage",
                    msg_id=10146047,
                    reply_to_msg_id=10146046,
                    event_type="message",
                    route_source="message:reply_context",
                    matched_text="【乱星海远航·归】\n侍妾【夜姬】已自 冒险 航线归来",
                    decision="skip_missing_identity",
                    now=1_781_074_993.0,
                )

                text = message_contract.get_message_contract_status_text(limit=10, latest=1, family="concubine_voyage")

        self.assertIn("只读", text)
        self.assertIn("日志群只提醒", text)
        self.assertIn("回 Codex 补规则", text)
        self.assertIn("不做确认", text)
        self.assertIn("契约缺口：1", text)
        self.assertIn("未匹配 handler：0", text)
        self.assertIn("reply_context_no_identity:1", text)
        self.assertIn("sample=contract_gap.concubine_voyage.message.10146047", text)


class ReplayFamilyCoverageTests(unittest.TestCase):
    def test_manifest_accepts_new_replay_module_aliases(self):
        self.assertEqual("侍妾远航", module_manifest.get_module_name_for_replay_module("concubine_voyage"))
        self.assertEqual("小世界", module_manifest.get_module_name_for_replay_module("small_world"))

    def test_replay_family_coverage_reports_missing_without_unknown_aliases(self):
        samples = {
            "voyage.start": {
                "source": "ledger:10140775",
                "module": "concubine_voyage",
                "family": "concubine_voyage",
                "event_type": "message",
                "text": "【乱星海远航·启】",
            }
        }

        validation = module_manifest.validate_replay_sample_coverage(samples)
        coverage = module_manifest.summarize_replay_family_coverage(samples)
        voyage_row = next(item for item in coverage["modules"] if item["module"] == "侍妾远航")

        self.assertEqual([], validation["unknown_sample_modules"])
        self.assertEqual([], validation["unknown_sample_families"])
        self.assertEqual(["concubine_voyage"], voyage_row["covered_families"])
        self.assertEqual([], voyage_row["missing_families"])
        self.assertFalse(coverage["ok"])
        self.assertTrue(any(item["family"] == "divination" for item in coverage["missing_families"]))


if __name__ == "__main__":
    unittest.main()
