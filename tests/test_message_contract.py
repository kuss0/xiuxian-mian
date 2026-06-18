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
    def test_verified_game_event_from_simple_namespace_preserves_routing_identity(self):
        event = SimpleNamespace(id=10140775, chat_id=-1001680975844, sender_id=8325841058)
        reply_context = {
            "send_as_id": 3504367852,
            "family": "concubine_voyage",
            "reply_to_msg_id": 10140774,
            "root_msg_id": 10140770,
            "reply_to_sender_id": 8325841058,
        }

        verified = message_contract.from_telegram_event(
            event,
            "【乱星海远航·启】\n预计归航时间：12小时 后",
            reply_context,
            event_kind="edit",
        )

        self.assertEqual("edit", verified.event_type)
        self.assertEqual(-1001680975844, verified.chat_id)
        self.assertEqual(10140775, verified.msg_id)
        self.assertEqual(8325841058, verified.sender_id)
        self.assertEqual(3504367852, verified.identity_id)
        self.assertEqual("concubine_voyage", verified.family)
        self.assertEqual(10140770, verified.root_msg_id)
        self.assertEqual("edit:reply_context", verified.route_source)
        self.assertEqual(8325841058, verified.reply_to_sender_id)

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

    def test_record_unhandled_routed_reply_accepts_verified_event_like_old_arguments(self):
        event = SimpleNamespace(id=10140775, chat_id=-1001680975844, sender_id=8325841058)
        text = "【乱星海远航·启】\n预计归航时间：12小时 后"
        reply_context = {
            "send_as_id": 3504367852,
            "family": "concubine_voyage",
            "reply_to_msg_id": 10140774,
            "root_msg_id": 10140774,
            "reply_to_sender_id": 8325841058,
        }
        verified = message_contract.from_telegram_event(event, text, reply_context, event_kind="edit")

        with patch.object(message_contract.passive_inbox, "record_passive_inbox_event", return_value=True) as inbox_mock:
            old_ok = message_contract.record_unhandled_routed_reply(
                event,
                text,
                3504367852,
                "concubine_voyage",
                10140774,
                event_kind="edit",
                reply_to_sender_id=8325841058,
            )
            verified_ok = message_contract.record_unhandled_routed_reply(verified)

        self.assertTrue(old_ok)
        self.assertTrue(verified_ok)
        self.assertEqual(2, inbox_mock.call_count)
        old_call = inbox_mock.call_args_list[0]
        verified_call = inbox_mock.call_args_list[1]
        self.assertEqual(old_call.args, verified_call.args)
        for key in (
            "module",
            "identity_id",
            "reason",
            "summary",
            "family",
            "chat_id",
            "msg_id",
            "reply_to_msg_id",
            "reply_to_sender_id",
            "root_msg_id",
            "event_type",
            "route_source",
            "matched_text",
            "decision",
            "source_message_id",
            "include_recent",
        ):
            self.assertEqual(old_call.kwargs[key], verified_call.kwargs[key])
        self.assertEqual("edit:reply_context", verified_call.kwargs["route_source"])
        self.assertEqual("concubine_voyage", verified_call.kwargs["family"])
        self.assertEqual(10140774, verified_call.kwargs["root_msg_id"])
        self.assertEqual(10140775, verified_call.kwargs["msg_id"])

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

    def test_unhandled_reply_resolved_by_later_passive_change_is_not_reported(self):
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
                    source_message_id=10140775,
                    reply_to_msg_id=10140774,
                    event_type="message",
                    route_source="message:reply_context",
                    matched_text="【乱星海远航·归】\n修为 +658",
                    decision="handler_not_matched",
                    now=1_781_077_200.0,
                )
                passive_event_ledger.append_passive_event(
                    kind="changed",
                    module="concubine",
                    identity_id=3504367852,
                    family="concubine_voyage",
                    msg_id=10140775,
                    source_message_id=10140775,
                    reply_to_msg_id=10140774,
                    event_type="message",
                    route_source="message:reply_context",
                    matched_text="【乱星海远航·归】\n修为 +658",
                    decision="state_changed",
                    now=1_781_077_201.0,
                )
                path = passive_event_ledger.get_passive_event_ledger_path(1_781_077_200.0)
                unhandled = list(message_contract.iter_unhandled_routed_replies(path=path, limit=10))
                gaps = list(message_contract.iter_message_contract_gaps(path=path, limit=10))

        self.assertEqual([], unhandled)
        self.assertEqual([], gaps)

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

    def test_report_tool_limit_can_scan_more_than_legacy_cap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ledger.jsonl"
            with path.open("w", encoding="utf-8") as fp:
                for offset in range(1205):
                    payload = {
                        "ts": 1_781_077_200.0 + offset,
                        "kind": "skipped",
                        "module": "侍妾远航",
                        "identity_id": 3504367852,
                        "reason": "unhandled_routed_reply",
                        "family": "concubine_voyage",
                        "msg_id": 10140000 + offset,
                        "message_id": 10140000 + offset,
                        "reply_to_msg_id": 10130000 + offset,
                        "event_type": "message",
                        "route_source": "message:reply_context",
                        "matched_text": "【乱星海远航·归】",
                        "decision": "handler_not_matched",
                    }
                    fp.write(json.dumps(payload, ensure_ascii=False) + "\n")

            out = io.StringIO()
            with redirect_stdout(out):
                code = message_contract_report.main([
                    "--ledger-path",
                    str(path),
                    "--limit",
                    "6000",
                    "--json",
                    "--latest",
                    "1",
                ])

        self.assertEqual(0, code)
        payload = json.loads(out.getvalue())
        self.assertEqual(6000, payload["limit"])
        self.assertEqual(6000, payload["effective_limit"])
        self.assertEqual(1205, payload["summary"]["total"])
        self.assertEqual({"侍妾远航": 1205}, payload["summary"]["by_module"])

    def test_report_tool_json_output_can_include_admission_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(passive_event_ledger, "PASSIVE_EVENT_LEDGER_DIR", tmpdir):
                path = passive_event_ledger.get_passive_event_ledger_path(1_781_077_200.0)

            out = io.StringIO()
            with redirect_stdout(out):
                code = message_contract_report.main([
                    "--ledger-path",
                    path,
                    "--json",
                    "--admission",
                    "--strict-module",
                    "太一",
                    "--strict-module",
                    "阴罗宗",
                ])

        self.assertEqual(0, code)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["admission"]["ok"], payload["admission"])
        self.assertEqual([], payload["admission"]["strict_missing_samples"])
        self.assertEqual([], payload["admission"]["passive_without_observation"])

    def test_report_tool_json_output_exposes_strict_family_sample_gaps(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "ledger.jsonl"
            ledger_path.write_text("", encoding="utf-8")

            out = io.StringIO()
            with redirect_stdout(out):
                code = message_contract_report.main([
                    "--ledger-path",
                    str(ledger_path),
                    "--json",
                    "--admission",
                    "--strict-module",
                    "合欢宗",
                ])

        self.assertEqual(0, code)
        payload = json.loads(out.getvalue())
        self.assertTrue(payload["admission"]["ok"], payload["admission"])
        self.assertEqual([], payload["admission"]["strict_missing_samples"])
        self.assertEqual(
            ["合欢宗:hehuan_escape"],
            payload["admission"]["strict_missing_sample_families"],
        )

    def test_report_tool_json_output_can_include_module_contract_matrix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "ledger.jsonl"
            ledger_path.write_text("", encoding="utf-8")

            out = io.StringIO()
            with redirect_stdout(out):
                code = message_contract_report.main([
                    "--ledger-path",
                    str(ledger_path),
                    "--json",
                    "--contracts",
                    "--strict-module",
                    "合欢宗",
                ])

        self.assertEqual(0, code)
        payload = json.loads(out.getvalue())
        contracts = payload["contracts"]
        rows = {row["module"]: row for row in contracts["modules"]}
        report_only = {row["feature_key"]: row for row in contracts["report_only"]["modules"]}
        self.assertEqual(len(tuple(module_manifest.iter_module_manifests())), contracts["totals"]["modules"])
        self.assertTrue(rows["合欢宗"]["strict"])
        self.assertFalse(report_only["auto_repair"]["scheduler_connected"])
        self.assertEqual(module_manifest.API_POLICY_BACKUP_ONLY, report_only["auto_repair"]["api_policy"])
        self.assertEqual("phase", rows["太一"]["duplicate_guard"])
        self.assertEqual([], rows["太一"]["missing_sample_families"])
        self.assertEqual(["hehuan_escape"], rows["合欢宗"]["missing_sample_families"])

    def test_report_tool_text_output_can_include_strict_module_contracts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "ledger.jsonl"
            ledger_path.write_text("", encoding="utf-8")

            out = io.StringIO()
            with redirect_stdout(out):
                code = message_contract_report.main([
                    "--ledger-path",
                    str(ledger_path),
                    "--contracts",
                    "--strict-module",
                    "合欢宗",
                ])

        self.assertEqual(0, code)
        text = out.getvalue()
        self.assertIn("模块合同:", text)
        self.assertIn("- 合欢宗: send=", text)
        self.assertIn("missing=hehuan_escape", text)

    def test_report_tool_text_output_can_include_report_only_contracts(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "ledger.jsonl"
            ledger_path.write_text("", encoding="utf-8")

            out = io.StringIO()
            with redirect_stdout(out):
                code = message_contract_report.main([
                    "--ledger-path",
                    str(ledger_path),
                    "--contracts",
                    "--strict-module",
                    "一键修理",
                ])

        self.assertEqual(0, code)
        text = out.getvalue()
        self.assertIn("未接入模块合同: 2 report-only, 2 API-backup", text)
        self.assertIn("- 一键修理: stage=report_only key=auto_repair api=backup_only", text)

    def test_rust_alignment_candidates_are_default_off_and_read_only(self):
        result = module_manifest.validate_rust_alignment_candidates()

        self.assertTrue(result["ok"], result)
        candidates = {
            candidate.feature_key: candidate
            for candidate in module_manifest.iter_rust_alignment_candidates()
        }
        self.assertEqual(
            {
                "local_status_views",
                "storage_bag_local_find",
                "inventory_transfer_planner",
                "local_getdata_snapshot",
                "spent_status_explicit_fetch",
                "replica_external_upload_query",
                "whois_peer_lookup",
                "miniapp_init_data",
                "inline_click_diagnostic",
                "operator_control_commands",
            },
            set(candidates),
        )
        self.assertTrue(candidates["local_status_views"].recommended_default_path)
        self.assertEqual(
            module_manifest.RUST_FEATURE_READ_ONLY_QUERY,
            candidates["storage_bag_local_find"].category,
        )
        self.assertEqual(
            module_manifest.API_POLICY_BACKUP_ONLY,
            candidates["spent_status_explicit_fetch"].api_policy,
        )
        self.assertFalse(candidates["whois_peer_lookup"].default_enabled)
        self.assertEqual(
            module_manifest.RUST_FEATURE_ACTIVE_ACTION,
            candidates["inventory_transfer_planner"].category,
        )
        self.assertFalse(candidates["inline_click_diagnostic"].scheduler_connected)
        self.assertEqual(
            (module_manifest.INPUT_SOURCE_OPERATOR_RPC,),
            candidates["whois_peer_lookup"].primary_inputs,
        )

    def test_module_contract_summary_includes_rust_alignment_candidates(self):
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"
        samples = json.loads(fixture_path.read_text(encoding="utf-8"))

        summary = module_manifest.summarize_module_contracts(
            samples,
            strict_modules=("太一", "阴罗宗", "一键修理", "local_status_views"),
        )
        rows = {row["feature_key"]: row for row in summary["rust_alignment"]["candidates"]}

        self.assertEqual(10, summary["rust_alignment"]["totals"]["candidates"])
        self.assertEqual(2, summary["rust_alignment"]["totals"]["backup_api_candidates"])
        self.assertEqual([], summary["rust_alignment"]["unknown_strict_candidates"])
        self.assertTrue(rows["local_status_views"]["recommended_default_path"])
        self.assertEqual(
            module_manifest.RUST_FEATURE_READ_ONLY_QUERY,
            rows["storage_bag_local_find"]["category"],
        )
        self.assertEqual(
            module_manifest.RUST_FEATURE_ACTIVE_ACTION,
            rows["inventory_transfer_planner"]["category"],
        )
        self.assertEqual(
            module_manifest.API_POLICY_BACKUP_ONLY,
            rows["spent_status_explicit_fetch"]["api_policy"],
        )

    def test_report_tool_text_output_can_include_rust_alignment_candidates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "ledger.jsonl"
            ledger_path.write_text("", encoding="utf-8")

            out = io.StringIO()
            with redirect_stdout(out):
                code = message_contract_report.main([
                    "--ledger-path",
                    str(ledger_path),
                    "--contracts",
                    "--strict-module",
                    "local_status_views",
                ])

        self.assertEqual(0, code)
        text = out.getvalue()
        self.assertIn("Rust 对照候选: 10 candidates", text)
        self.assertIn("- 本地状态多视图: cmd=status root|sect|dongfu|companion|dungeon|smallworld", text)
        self.assertIn("category=read_only_query", text)

    def test_report_tool_json_output_can_include_module_readiness_backlog(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "ledger.jsonl"
            ledger_path.write_text("", encoding="utf-8")

            out = io.StringIO()
            with redirect_stdout(out):
                code = message_contract_report.main([
                    "--ledger-path",
                    str(ledger_path),
                    "--json",
                    "--readiness",
                    "--strict-module",
                    "灵树",
                ])

        self.assertEqual(0, code)
        payload = json.loads(out.getvalue())
        readiness = payload["readiness"]
        rows = {row["module"]: row for row in readiness["modules"]}
        self.assertEqual(30, readiness["totals"]["sample_complete_modules"])
        self.assertEqual(1, readiness["totals"]["sample_partial_modules"])
        self.assertEqual(0, readiness["totals"]["sample_missing_modules"])
        self.assertEqual(3, readiness["totals"]["contract_only_modules"])
        self.assertEqual(1, readiness["totals"]["archived_modules"])
        self.assertTrue(rows["灵树"]["strict"])
        self.assertTrue(rows["灵树"]["archived"])
        self.assertEqual(module_manifest.READINESS_ARCHIVED, rows["灵树"]["readiness"])

    def test_report_tool_json_output_can_include_gap_classes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(passive_event_ledger, "PASSIVE_EVENT_LEDGER_DIR", tmpdir):
                passive_event_ledger.append_passive_event(
                    kind="skipped",
                    reason="external_identity_no_match",
                    module="wild_training",
                    family="wild_training",
                    msg_id=10146047,
                    matched_text="【野外历练】 @other 正向荒野深处行去...",
                    now=1_781_077_200.0,
                )
                ledger_path = passive_event_ledger.get_passive_event_ledger_path(1_781_077_200.0)

            out = io.StringIO()
            with redirect_stdout(out):
                code = message_contract_report.main([
                    "--ledger-path",
                    str(ledger_path),
                    "--json",
                    "--latest",
                    "1",
                ])

        self.assertEqual(0, code)
        payload = json.loads(out.getvalue())
        self.assertEqual(1, payload["gap_summary"]["external_observation_total"])
        self.assertEqual(0, payload["gap_summary"]["needs_attention_total"])
        self.assertEqual({"external_observation": 1}, payload["gap_summary"]["by_class"])

    def test_report_tool_text_output_can_include_module_readiness_backlog(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ledger_path = Path(tmpdir) / "ledger.jsonl"
            ledger_path.write_text("", encoding="utf-8")

            out = io.StringIO()
            with redirect_stdout(out):
                code = message_contract_report.main([
                    "--ledger-path",
                    str(ledger_path),
                    "--readiness",
                    "--strict-module",
                    "天星宗",
                ])

        self.assertEqual(0, code)
        text = out.getvalue()
        fixture_path = PROJECT_ROOT / "tests" / "fixtures" / "real_message_samples.json"
        samples = json.loads(fixture_path.read_text(encoding="utf-8"))
        readiness = module_manifest.summarize_module_readiness(samples, strict_modules=("天星宗",))
        totals = readiness["totals"]
        expected = (
            "模块就绪度: "
            f"complete={totals['sample_complete_modules']}, "
            f"partial={totals['sample_partial_modules']}, "
            f"missing={totals['sample_missing_modules']}, "
            f"contract-only={totals['contract_only_modules']}, "
            f"archived={totals['archived_modules']}, "
            f"families={totals['covered_sample_families']}/{totals['reply_families']}"
        )
        self.assertIn(expected, text)
        self.assertIn("- 天星宗: sample_complete 9/9", text)

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

    def test_contract_gap_summary_classifies_external_observations(self):
        events = [
            {
                "kind": "skipped",
                "reason": "reply_context_no_identity",
                "family": "tower",
                "msg_id": 10146047,
                "matched_text": "【琉璃问心塔】",
            },
            {
                "kind": "skipped",
                "reason": "external_identity_no_match",
                "family": "wild_training",
                "msg_id": 10146048,
                "matched_text": "【野外历练】 @other 正向荒野深处行去...",
            },
            {
                "kind": "skipped",
                "reason": "unhandled_routed_reply",
                "family": "concubine_voyage",
                "msg_id": 10146049,
                "matched_text": "【乱星海远航·归】",
                "decision": "handler_not_matched",
            },
        ]

        summary = message_contract.summarize_message_contract_gaps(events)

        self.assertEqual(3, summary["total"])
        self.assertEqual(2, summary["needs_attention_total"])
        self.assertEqual(1, summary["external_observation_total"])
        self.assertEqual(
            {
                "external_observation": 1,
                "handler_gap": 1,
                "unresolved_identity": 1,
            },
            summary["by_class"],
        )
        self.assertEqual(
            "external_observation",
            message_contract.classify_message_contract_gap(events[1]),
        )

    def test_contract_gap_summary_treats_external_sender_as_observation(self):
        event = {
            "kind": "skipped",
            "reason": "reply_context_no_identity",
            "family": "concubine_status",
            "msg_id": 10146050,
            "reply_to_sender_id": -1003356857743,
            "matched_text": "你的道心侍妾: 【紫灵】 (状态: 随行中)",
            "decision": "skip_missing_identity",
        }

        summary = message_contract.summarize_message_contract_gaps([event])

        self.assertEqual(1, summary["total"])
        self.assertEqual(0, summary["needs_attention_total"])
        self.assertEqual(1, summary["external_observation_total"])
        self.assertEqual({"external_observation": 1}, summary["by_class"])

    def test_contract_gap_summary_treats_ownerless_no_context_as_weak_hint(self):
        event = {
            "kind": "skipped",
            "reason": "no_reply_context",
            "family": "",
            "msg_id": 10146051,
            "matched_text": "【闭关成功】\n你福至心灵，成功炼化灵气。",
            "decision": "skip_missing_identity",
        }

        summary = message_contract.summarize_message_contract_gaps([event])

        self.assertEqual(1, summary["total"])
        self.assertEqual(0, summary["needs_attention_total"])
        self.assertEqual(1, summary["weak_owner_hint_total"])
        self.assertEqual({"weak_owner_hint": 1}, summary["by_class"])

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
        self.assertIn("待修/待归因：1；外部观察：0；弱归属：0", text)
        self.assertIn("未匹配 handler：0", text)
        self.assertIn("按分类：unresolved_identity:1", text)
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
