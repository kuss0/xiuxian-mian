import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import action_guard, runtime
from model import state as state_module
from model.features import passive_event_ledger, passive_inbox, workflow_log


class PassiveInboxEvidenceTests(unittest.TestCase):
    def setUp(self):
        self._stats_snapshot = copy.deepcopy(passive_inbox._passive_stats)
        self._observed_snapshot = dict(passive_inbox._observed_passive_events)
        passive_inbox._passive_stats = {
            "total": 0,
            "changed": 0,
            "skipped": 0,
            "modules": {},
            "skip_reasons": {},
            "recent": [],
        }
        passive_inbox._observed_passive_events = {}

    def tearDown(self):
        passive_inbox._passive_stats = self._stats_snapshot
        passive_inbox._observed_passive_events = self._observed_snapshot

    def test_you_marker_line_can_route_tree_panel_without_reply_identity(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        identity_id = 3800619925
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username="growrdick", label="丁丁", daohao="随缘子")
            event = SimpleNamespace(chat_id=-1001680975844, id=9512605)
            text = "\n".join([
                "【落云宗 · 灵眼之树】",
                "✨ 状态: 成熟采摘期",
                "🏆 本轮最终分枝榜 (天道快照):",
                "7. growrdick (你): 1039 ⏳(未领)",
                "",
                "👤 你的当前状态: 1039 点",
            ])

            with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
                handled = asyncio.run(passive_inbox.handle_passive_module_card(
                    text,
                    now=1_779_978_314.0,
                    reply_context={"family": "tree_panel", "reply_to_msg_id": 9512604, "root_msg_id": 9512604},
                    event=event,
                    event_type="message",
                ))

            self.assertTrue(handled)
            with state_module.use_identity(identity_id):
                self.assertTrue(state_module.state["is_maturing"])
                self.assertFalse(state_module.state["pending_irrigation"])
            snapshot = passive_inbox.get_passive_inbox_snapshot()
            self.assertEqual(1, snapshot["changed"])
            self.assertEqual(1, snapshot["modules"]["tree"])
            self.assertEqual(identity_id, snapshot["recent"][-1]["identity_id"])
            self.assertEqual("message:passive_you_line", snapshot["recent"][-1]["route_source"])
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_you_marker_line_requires_unique_identity_match(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            for identity_id in (1001, 1002):
                state_module.ensure_identity_registered(identity_id)
                state_module.update_send_as_profile(identity_id, username="sameuser", label=f"same-{identity_id}")

            event = SimpleNamespace(chat_id=-1001680975844, id=9512606)
            text = "【落云宗 · 灵眼之树】\n🏆 本轮最终分枝榜 (天道快照):\n7. sameuser (你): 1039 ⏳(未领)\n👤 你的当前状态: 1039 点"

            with patch.object(passive_inbox, "_save_passive_stats"):
                handled = asyncio.run(passive_inbox.handle_passive_module_card(
                    text,
                    now=1_779_978_314.0,
                    reply_context={"family": "tree_panel", "reply_to_msg_id": 9512604, "root_msg_id": 9512604},
                    event=event,
                    event_type="message",
                ))

            self.assertFalse(handled)
            snapshot = passive_inbox.get_passive_inbox_snapshot()
            self.assertEqual(1, snapshot["skipped"])
            self.assertEqual(1, snapshot["skip_reasons"]["reply_context_no_identity"])
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

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

    def test_reply_context_without_identity_counts_without_recent_noise(self):
        event = SimpleNamespace(chat_id=-1001680975844, id=9512505)
        with patch.object(passive_inbox, "_save_passive_stats"):
            handled = asyncio.run(passive_inbox.handle_passive_module_card(
                "【琉璃问心塔】\n你深吸一口气，踏入了古塔的第 1 层。",
                now=1_779_978_314.0,
                reply_context={"family": "tower", "reply_to_msg_id": 9512504, "root_msg_id": 9512504},
                event=event,
                event_type="message",
            ))

        self.assertFalse(handled)
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["total"])
        self.assertEqual(1, snapshot["skipped"])
        self.assertEqual(1, snapshot["skip_reasons"]["reply_context_no_identity"])
        self.assertEqual([], snapshot["recent"])

    def test_duplicate_same_message_text_is_ignored(self):
        event = SimpleNamespace(chat_id=-1001680975844, id=9512505)
        text = "【琉璃问心塔】\n你深吸一口气，踏入了古塔的第 1 层。"
        reply_context = {"family": "tower", "reply_to_msg_id": 9512504, "root_msg_id": 9512504}

        with patch.object(passive_inbox, "_save_passive_stats"):
            first = asyncio.run(passive_inbox.handle_passive_module_card(
                text,
                now=1_779_978_314.0,
                reply_context=reply_context,
                event=event,
                event_type="message",
            ))
            second = asyncio.run(passive_inbox.handle_passive_module_card(
                text,
                now=1_779_978_315.0,
                reply_context=reply_context,
                event=event,
                event_type="message",
            ))

        self.assertFalse(first)
        self.assertFalse(second)
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["total"])
        self.assertEqual(1, snapshot["skip_reasons"]["reply_context_no_identity"])

    def test_same_message_with_edited_text_is_not_deduped(self):
        event = SimpleNamespace(chat_id=-1001680975844, id=9512505)
        reply_context = {"family": "tower", "reply_to_msg_id": 9512504, "root_msg_id": 9512504}

        with patch.object(passive_inbox, "_save_passive_stats"):
            asyncio.run(passive_inbox.handle_passive_module_card(
                "【琉璃问心塔】\n你深吸一口气，踏入了古塔的第 1 层。",
                now=1_779_978_314.0,
                reply_context=reply_context,
                event=event,
                event_type="message",
            ))
            asyncio.run(passive_inbox.handle_passive_module_card(
                "【试炼古塔 - 战报】\n本次共闯过 21 层。",
                now=1_779_978_328.0,
                reply_context=reply_context,
                event=event,
                event_type="edit",
            ))

        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(2, snapshot["total"])
        self.assertEqual(2, snapshot["skip_reasons"]["reply_context_no_identity"])

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

    def test_passive_event_ledger_records_structured_evidence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(passive_event_ledger, "PASSIVE_EVENT_LEDGER_DIR", tmpdir),
                patch.object(passive_inbox, "_save_passive_stats"),
            ):
                ok = passive_inbox.record_passive_inbox_event(
                    "changed",
                    module="taiyi",
                    identity_id=8659059191,
                    family="taiyi_yindao",
                    chat_id=-1001680975844,
                    msg_id=9446794,
                    reply_to_msg_id=9446793,
                    reply_to_sender_id=8659059191,
                    event_type="edit",
                    route_source="edit:reply_context",
                    decision="calibrate_manual_late_no_search",
                    matched_text="你引动【水之道】，获得了 100点神识！",
                    state_before="waiting_yindao",
                    state_after="idle",
                    source_message_id=9446794,
                )

            self.assertTrue(ok)
            ledger_files = list(Path(tmpdir).glob("*.jsonl"))
            self.assertEqual(1, len(ledger_files))
            payload = json.loads(ledger_files[0].read_text(encoding="utf-8").strip())

        self.assertEqual("changed", payload["kind"])
        self.assertEqual("taiyi", payload["module"])
        self.assertEqual("taiyi_yindao", payload["family"])
        self.assertEqual(-1001680975844, payload["chat_id"])
        self.assertEqual(9446794, payload["msg_id"])
        self.assertEqual(9446793, payload["reply_to_msg_id"])
        self.assertEqual(8659059191, payload["reply_to_sender_id"])
        self.assertEqual("edit", payload["event_type"])
        self.assertEqual("edit:reply_context", payload["route_source"])
        self.assertEqual("waiting_yindao", payload["state_before"])
        self.assertEqual("idle", payload["state_after"])
        self.assertIn("matched_text_hash", payload)

    def test_passive_event_ledger_uses_test_state_dir_from_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.dict(os.environ, {"XIUXIAN_STATE_DIR": tmpdir}),
                patch.object(passive_event_ledger, "PASSIVE_EVENT_LEDGER_DIR", passive_event_ledger._DEFAULT_PASSIVE_EVENT_LEDGER_DIR),
            ):
                path = passive_event_ledger.get_passive_event_ledger_path(1_779_911_737.0)

        self.assertTrue(str(Path(path)).startswith(tmpdir))

    def test_passive_event_ledger_iter_skips_bad_lines(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(passive_event_ledger, "PASSIVE_EVENT_LEDGER_DIR", tmpdir):
                ok = passive_event_ledger.append_passive_event(
                    kind="changed",
                    module="taiyi",
                    identity_id=8659059191,
                    family="taiyi_yindao",
                    msg_id=9446794,
                    matched_text="你引动【水之道】，获得了 100点神识！",
                    decision="calibrate_manual_late_no_search",
                    now=1_779_911_737.0,
                )
                path = passive_event_ledger.get_passive_event_ledger_path(1_779_911_737.0)
                with open(path, "a", encoding="utf-8") as fp:
                    fp.write("not-json\n")

                events = passive_event_ledger.iter_passive_events(path=path, limit=10)

        self.assertTrue(ok)
        self.assertEqual(1, len(events))
        self.assertEqual("taiyi", events[0]["module"])
        self.assertEqual("taiyi_yindao", events[0]["family"])


class WorkflowLogUtilityTests(unittest.TestCase):
    def test_workflow_log_roundtrip_uses_test_state_dir_from_env(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.dict(os.environ, {"XIUXIAN_STATE_DIR": tmpdir}),
                patch.object(workflow_log, "WORKFLOW_LOG_DIR", workflow_log._DEFAULT_WORKFLOW_LOG_DIR),
            ):
                ok = workflow_log.append_workflow_event(
                    "storage/bag transfer",
                    op_id="transfer-1",
                    event="购买已发送",
                    status="changed",
                    identity_id=1001,
                    msg_id=22028,
                    family="storage_bag_buy",
                    command=".购买 22028",
                    detail={"listing_id": "22028", "empty": ""},
                    now=1_779_911_737.0,
                )
                path = workflow_log.get_workflow_log_path("storage/bag transfer", 1_779_911_737.0)
                events = workflow_log.iter_workflow_events("storage/bag transfer", path=path, limit=10)

        self.assertTrue(ok)
        self.assertTrue(str(Path(path)).startswith(tmpdir))
        self.assertEqual(1, len(events))
        self.assertEqual("storage_bag_transfer", events[0]["workflow"])
        self.assertEqual("storage_bag_buy", events[0]["family"])
        self.assertEqual("22028", events[0]["detail"]["listing_id"])


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

    def test_send_intent_infers_modules_for_parameterized_commands(self):
        cases = {
            ".购买 22028": "储物袋",
            ".上架 凝血草 1 换 筑基丹*1": "储物袋",
            ".赠送 筑基丹 1": "储物袋",
            ".加入苍坤洞府 393": "自动副本",
            ".稳": "共历心劫",
            ".天机代卜": "天机代卜",
            ".搜寻节点": "太一",
            ".查看闭关": "深度闭关",
        }

        with patch.object(runtime, "is_auto_delete_sent_messages_enabled", return_value=False):
            for command, source_module in cases.items():
                with self.subTest(command=command):
                    intent = runtime._normalize_send_intent(command)

                    self.assertEqual(source_module, intent["source_module"])
                    self.assertEqual("keep", intent["delete_policy"])

    def test_send_intent_infers_three_sect_modules(self):
        cases = {
            ".双修 温养": "合欢宗",
            ".天机盘": "天星宗",
            ".观命": "天星宗",
            ".定命 天府": "天星宗",
            ".推命 炼制": "天星宗",
            ".改命 探索": "天星宗",
            ".消劫": "天星宗",
            ".我的阴罗幡": "阴罗宗",
            ".血洗山林": "阴罗宗",
            ".召唤魔影": "阴罗宗",
            ".化功为煞 1000": "阴罗宗",
            ".收取精华 1": "阴罗宗",
            ".囚禁魂魄 2 妖兽精魄": "阴罗宗",
        }

        with patch.object(runtime, "is_auto_delete_sent_messages_enabled", return_value=False):
            for command, source_module in cases.items():
                with self.subTest(command=command):
                    intent = runtime._normalize_send_intent(command)

                    self.assertEqual(source_module, intent["source_module"])

    def test_action_guard_resolves_three_sect_parameterized_commands(self):
        cases = {
            ".双修 温养": "hehuan_dual",
            ".天机盘": "tianxing_panel",
            ".观命": "tianxing_observe",
            ".定命 天府": "tianxing_set_star",
            ".推命 炼制": "tianxing_predict",
            ".改命 探索": "tianxing_change_fate",
            ".消劫": "tianxing_clear_calamity",
            ".我的阴罗幡": "yinluo_banner",
            ".血洗山林": "yinluo_blood_forest",
            ".召唤魔影": "yinluo_demon_summon",
            ".化功为煞 1000": "yinluo_convert",
            ".收取精华 1": "yinluo_collect",
            ".囚禁魂魄 2 妖兽精魄": "yinluo_refine",
            ".下咒 @target": "yinluo_curse",
            ".夺舍 @target": "yinluo_possess",
        }

        for command, action_key in cases.items():
            with self.subTest(command=command):
                self.assertEqual(action_key, action_guard.resolve_action_key(command))

        self.assertEqual("yinluo_demon_summon", action_guard.resolve_action_key_for_family("yinluo_demon_summon"))
        self.assertEqual("tianxing_change_fate", action_guard.resolve_action_key_for_family("tianxing_change_fate"))

    def test_action_guard_closes_three_sect_sessions_by_reply_family(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        identity_id = 990301
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(identity_id)
            commands = {
                ".双修 温养": "hehuan_dual",
                ".改命 探索": "tianxing_change_fate",
                ".召唤魔影": "yinluo_demon_summon",
            }
            for command, family in commands.items():
                with self.subTest(command=command):
                    action_guard.note_sent(command, identity_id, 100, sent_at=1_780_000_000.0)
                    self.assertIn(action_guard.resolve_action_key(command), action_guard.get_action_guard_sessions(identity_id))

                    self.assertTrue(action_guard.close_by_family(family, send_as_id=identity_id, now=1_780_000_010.0))
                    self.assertNotIn(action_guard.resolve_action_key(command), action_guard.get_action_guard_sessions(identity_id))
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)


if __name__ == "__main__":
    unittest.main()
