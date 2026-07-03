import asyncio
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import action_guard, app, message_contract, runtime
from model import state as state_module
from model.features import passive_event_ledger, passive_inbox, workflow_log
from model.verified_event import from_telegram_event


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

    def test_verified_event_can_route_tree_panel_without_reply_identity(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        identity_id = 3800619925
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username="growrdick", label="丁丁", daohao="随缘子")
            event = SimpleNamespace(chat_id=-1001680975844, id=9512605, sender_id=8325841058)
            text = "\n".join([
                "【落云宗 · 灵眼之树】",
                "✨ 状态: 成熟采摘期",
                "🏆 本轮最终分枝榜 (天道快照):",
                "7. growrdick (你): 1039 ⏳(未领)",
                "",
                "👤 你的当前状态: 1039 点",
            ])
            verified = from_telegram_event(
                event,
                text,
                {"family": "tree_panel", "reply_to_msg_id": 9512604, "root_msg_id": 9512604},
                event_kind="message",
            )

            with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
                handled = asyncio.run(passive_inbox.handle_passive_module_card(
                    verified,
                    now=1_779_978_314.0,
                ))

            self.assertTrue(handled)
            snapshot = passive_inbox.get_passive_inbox_snapshot()
            self.assertEqual(1, snapshot["changed"])
            self.assertEqual(identity_id, snapshot["recent"][-1]["identity_id"])
            self.assertEqual(9512605, snapshot["recent"][-1]["msg_id"])
            self.assertEqual(9512604, snapshot["recent"][-1]["reply_to_msg_id"])
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

    def test_reply_sender_identity_routes_passive_concubine_panel(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        identity_id = 3800619925
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username="growrdick", label="丁丁", daohao="随缘子")
            event = SimpleNamespace(chat_id=-1001680975844, id=9512607)
            text = (
                "你的道心侍妾: 【紫灵】 (状态: 随行中)\n\n"
                "情缘值: 479\n"
                "【第二期机缘】\n"
                "- 入梦寻图冷却: 可施展\n"
                "- 共历心劫冷却: 可施展\n"
                "- 天机代卜冷却: 可施展\n"
                "命令: .入梦寻图、.残图、.拼图、.共历心劫、.坠魔心劫、.天机代卜"
            )

            with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
                handled = asyncio.run(passive_inbox.handle_passive_module_card(
                    text,
                    now=1_779_978_314.0,
                    reply_context={
                        "family": "concubine_status",
                        "reply_to_msg_id": 9512606,
                        "root_msg_id": 9512606,
                        "reply_to_sender_id": -1003800619925,
                    },
                    event=event,
                    event_type="message",
                ))

            self.assertTrue(handled)
            with state_module.use_identity(identity_id):
                self.assertEqual("紫灵", state_module.state["concubine_name"])
                self.assertEqual(479, state_module.state["concubine_affinity"])
                self.assertEqual(9512607, state_module.state["concubine_last_panel_msg_id"])
            snapshot = passive_inbox.get_passive_inbox_snapshot()
            self.assertEqual(1, snapshot["changed"])
            self.assertEqual(identity_id, snapshot["recent"][-1]["identity_id"])
            self.assertEqual("message:reply_sender", snapshot["recent"][-1]["route_source"])
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_external_reply_sender_still_skips_without_identity(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(3800619925)
            event = SimpleNamespace(chat_id=-1001680975844, id=9512608)

            with patch.object(passive_inbox, "_save_passive_stats"):
                handled = asyncio.run(passive_inbox.handle_passive_module_card(
                    "你的道心侍妾: 【外部】 (状态: 随行中)\n\n情缘值: 479",
                    now=1_779_978_314.0,
                    reply_context={
                        "family": "concubine_status",
                        "reply_to_msg_id": 9512606,
                        "root_msg_id": 9512606,
                        "reply_to_sender_id": -1003356857743,
                    },
                    event=event,
                    event_type="message",
                ))

            self.assertFalse(handled)
            snapshot = passive_inbox.get_passive_inbox_snapshot()
            self.assertEqual(1, snapshot["skipped"])
            self.assertEqual(1, snapshot["skip_reasons"]["external_identity_no_match"])
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

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

    def test_duplicate_verified_event_text_is_ignored(self):
        event = SimpleNamespace(chat_id=-1001680975844, id=9512505, sender_id=8325841058)
        text = "【琉璃问心塔】\n你深吸一口气，踏入了古塔的第 1 层。"
        verified = from_telegram_event(
            event,
            text,
            {"family": "tower", "reply_to_msg_id": 9512504, "root_msg_id": 9512504},
            event_kind="message",
        )

        with patch.object(passive_inbox, "_save_passive_stats"):
            first = asyncio.run(passive_inbox.handle_passive_module_card(
                verified,
                now=1_779_978_314.0,
            ))
            second = asyncio.run(passive_inbox.handle_passive_module_card(
                verified,
                now=1_779_978_315.0,
            ))

        self.assertFalse(first)
        self.assertFalse(second)
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["total"])
        self.assertEqual(1, snapshot["skip_reasons"]["reply_context_no_identity"])

    def test_routed_wild_training_reply_is_not_applied_again_by_passive_inbox(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        identity_id = 3907536807
        text = "\n".join([
            "【野外历练 · 妖兽遭遇】",
            "@sanshaoyedejian1 遭遇 赤焰妖虎。",
            "战力对比: 你 418738 / 妖兽 341879，胜算 70%。",
            "一番斗法后，妖兽伏诛。",
            "获得修为 +4486，获得 【二级妖丹】x1。",
            "此战只结算 NPC 历练收益，不触发玩家仇怨。",
        ])
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(identity_id)
            state_module.update_send_as_profile(identity_id, username="sanshaoyedejian1", label="三少爷的剑")
            with state_module.use_identity(identity_id) as identity_state:
                identity_state["wild_training_last_result"] = "旧结果"
                identity_state["next_wild_training_time"] = 0

            routed_event = SimpleNamespace(chat_id=-1001680975844, id=11387898, sender_id=8547797815)
            routed_context = {
                "send_as_id": identity_id,
                "family": "wild_training",
                "reply_to_msg_id": 11387896,
                "root_msg_id": 11387896,
                "routed_reply_handled": True,
            }
            with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
                handled = asyncio.run(passive_inbox.handle_passive_module_card(
                    text,
                    now=1_783_060_378.0,
                    reply_context=routed_context,
                    event=routed_event,
                    event_type="edit",
                ))

            self.assertFalse(handled)
            with state_module.use_identity(identity_id):
                self.assertEqual("旧结果", state_module.state["wild_training_last_result"])
                self.assertEqual(0, state_module.state["next_wild_training_time"])
            snapshot = passive_inbox.get_passive_inbox_snapshot()
            self.assertEqual(0, snapshot["changed"])

            passive_event = SimpleNamespace(chat_id=-1001680975844, id=11387999, sender_id=8547797815)
            passive_context = {
                "send_as_id": identity_id,
                "family": "wild_training",
                "reply_to_msg_id": 11387997,
                "root_msg_id": 11387997,
            }
            with patch.object(passive_inbox, "_save_passive_stats"), patch.object(passive_inbox, "save_state"):
                handled = asyncio.run(passive_inbox.handle_passive_module_card(
                    text,
                    now=1_783_060_400.0,
                    reply_context=passive_context,
                    event=passive_event,
                    event_type="edit",
                ))

            self.assertTrue(handled)
            with state_module.use_identity(identity_id):
                self.assertEqual("修为+4486 ｜ 奖励:二级妖丹x1", state_module.state["wild_training_last_result"])
                self.assertGreater(state_module.state["next_wild_training_time"], 1_783_060_400.0)
            snapshot = passive_inbox.get_passive_inbox_snapshot()
            self.assertEqual(1, snapshot["changed"])
            self.assertEqual(1, snapshot["modules"]["wild_training"])
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

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

    def test_changed_event_resolves_prior_unhandled_routed_reply(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(passive_event_ledger, "PASSIVE_EVENT_LEDGER_DIR", tmpdir):
                passive_event_ledger.append_passive_event(
                    kind="skipped",
                    module="小世界",
                    identity_id=8659059191,
                    reason=message_contract.UNHANDLED_ROUTED_REPLY_REASON,
                    summary="small_world_harvest",
                    family="small_world_harvest",
                    msg_id=10480363,
                    reply_to_msg_id=10480362,
                    root_msg_id=10480362,
                    event_type="message",
                    route_source="message:reply_context",
                    matched_text="你大手一挥，将凡间供奉的 3855 点香火尽数收入紫府。\n当前香火库存: 20326",
                    decision=message_contract.UNHANDLED_ROUTED_REPLY_DECISION,
                    source_message_id=10480363,
                    now=1_781_637_921.0,
                )
                passive_event_ledger.append_passive_event(
                    kind="changed",
                    module="small_world",
                    identity_id=8659059191,
                    summary="small_world_harvest",
                    family="small_world_harvest",
                    msg_id=10480363,
                    reply_to_msg_id=10480362,
                    root_msg_id=10480362,
                    event_type="message",
                    route_source="message:reply_context",
                    matched_text="你大手一挥，将凡间供奉的 3855 点香火尽数收入紫府。\n当前香火库存: 20326",
                    decision="state_changed",
                    source_message_id=10480363,
                    now=1_781_637_922.0,
                )
                path = passive_event_ledger.get_passive_event_ledger_path(1_781_637_921.0)

                unhandled = list(message_contract.iter_unhandled_routed_replies(path=path, limit=10))
                gaps = list(message_contract.iter_message_contract_gaps(path=path, limit=10))

        self.assertEqual([], unhandled)
        self.assertEqual([], gaps)

    def test_passive_inbox_snapshot_attention_excludes_resolved_unhandled_reply(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(passive_event_ledger, "PASSIVE_EVENT_LEDGER_DIR", tmpdir),
                patch.object(passive_inbox, "_save_passive_stats"),
            ):
                ok = passive_inbox.record_passive_inbox_event(
                    "skipped",
                    module="侍妾远航",
                    identity_id=3504367852,
                    reason=message_contract.UNHANDLED_ROUTED_REPLY_REASON,
                    summary="concubine_voyage",
                    family="concubine_voyage",
                    msg_id=10140775,
                    source_message_id=10140775,
                    reply_to_msg_id=10140774,
                    event_type="message",
                    route_source="message:reply_context",
                    matched_text="【乱星海远航·归】\n修为 +658",
                    decision=message_contract.UNHANDLED_ROUTED_REPLY_DECISION,
                )
                self.assertTrue(ok)
                ok = passive_inbox.record_passive_inbox_event(
                    "changed",
                    module="concubine",
                    identity_id=3504367852,
                    summary="concubine_voyage",
                    family="concubine_voyage",
                    msg_id=10140775,
                    source_message_id=10140775,
                    reply_to_msg_id=10140774,
                    event_type="message",
                    route_source="message:reply_context",
                    matched_text="【乱星海远航·归】\n修为 +658",
                    decision="state_changed",
                )
                self.assertTrue(ok)

                snapshot = passive_inbox.get_passive_inbox_snapshot()

        self.assertEqual(2, snapshot["total"])
        self.assertEqual(1, snapshot["changed"])
        self.assertEqual(1, snapshot["skipped"])
        self.assertEqual(0, snapshot["attention_total"])
        self.assertEqual({}, snapshot["attention_by_class"])
        self.assertEqual({}, snapshot["attention_by_reason"])

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
            ".元婴闭关": "元婴",
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
            ".炼制 玄铁剑": "天星宗",
            ".我的阴罗幡": "阴罗宗",
            ".每日献祭": "阴罗宗",
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
            ".炼制 玄铁剑": "tianxing_craft_farm",
            ".元婴闭关": "yuanying_launch",
            ".探寻裂缝": "explore_rift",
            ".我的阴罗幡": "yinluo_banner",
            ".每日献祭": "yinluo_daily_sacrifice",
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
        self.assertEqual("wendao", action_guard.resolve_action_key_for_family("wendao"))
        self.assertEqual("explore_rift", action_guard.resolve_action_key_for_family("explore_rift"))

    def test_action_guard_resolves_module_owned_sessions(self):
        tianxing_keys = set(action_guard.resolve_action_keys_for_module("天星宗"))

        self.assertTrue(
            {
                "tianxing_panel",
                "tianxing_observe",
                "tianxing_set_star",
                "tianxing_predict",
                "tianxing_change_fate",
                "tianxing_clear_calamity",
                "tianxing_retreat_farm",
                "tianxing_craft_farm",
                "tianxing_heqi_dan",
            }.issubset(tianxing_keys)
        )
        self.assertNotIn("deep_retreat", tianxing_keys)

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
                ".探寻裂缝": "explore_rift",
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

    def test_action_guard_keeps_short_same_command_guard_after_reply_close(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        identity_id = 990304
        try:
            action_guard._recent_closed_command_guards.clear()
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(identity_id)

            action_guard.note_sent(".显灵", identity_id, 100, sent_at=1_780_000_000.0)
            self.assertTrue(action_guard.close_by_family("small_world_manifest", send_as_id=identity_id, now=1_780_000_010.0))
            self.assertNotIn("small_world_manifest", action_guard.get_action_guard_sessions(identity_id))

            allowed, reason = action_guard.before_send(".显灵", send_as_id=identity_id, now=1_780_000_020.0)
            self.assertFalse(allowed)
            self.assertIn("短窗保护", reason)

            allowed, _reason = action_guard.before_send(".显灵", send_as_id=identity_id, now=1_780_000_096.0)
            self.assertTrue(allowed)
        finally:
            action_guard._recent_closed_command_guards.clear()
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_action_guard_reopens_stale_closed_session_after_timeout_cleanup(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        identity_id = 990307
        now = 1_780_000_200.0
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["wanxin_observation"] = {"pending": {}}
                state_module.state["action_guard_sessions"] = {
                    "wanxin_protect": {
                        "action_key": "wanxin_protect",
                        "label": "护持神魂",
                        "attempt": 1,
                        "first_sent_at": now - 180,
                        "last_sent_at": now - 180,
                        "next_allowed_at": 0,
                        "last_msg_id": 7201,
                        "last_command": ".护持神魂",
                        "closed_at": now - 60,
                        "close_reason": "wanxin_timeout",
                    }
                }

            allowed, reason = action_guard.before_send(".护持神魂", send_as_id=identity_id, now=now)

            self.assertTrue(allowed, reason)
            with state_module.use_identity(identity_id):
                session = state_module.state["action_guard_sessions"].get("wanxin_protect") or {}
                self.assertEqual(0, int(session.get("attempt", 0) or 0))
                self.assertEqual(0, float(session.get("closed_at", 0) or 0))
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_action_guard_keeps_closed_remote_block_until_remote_expiry(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        identity_id = 990308
        now = 1_780_000_300.0
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["wanxin_observation"] = {"pending": {}}
                state_module.state["action_guard_sessions"] = {
                    "wanxin_protect": {
                        "action_key": "wanxin_protect",
                        "label": "护持神魂",
                        "attempt": 0,
                        "first_sent_at": now - 180,
                        "last_sent_at": now - 180,
                        "next_allowed_at": 0,
                        "last_msg_id": 0,
                        "last_command": ".护持神魂",
                        "closed_at": now - 60,
                        "close_reason": "cooldown",
                        "remote_block_until": now + 600,
                        "remote_block_reason": "游戏提示冷却中",
                        "remote_block_kind": "cooldown",
                    }
                }

            allowed, reason = action_guard.before_send(".护持神魂", send_as_id=identity_id, now=now)

            self.assertFalse(allowed)
            self.assertIn("冷却", reason)
            with state_module.use_identity(identity_id):
                self.assertIn("wanxin_protect", state_module.state["action_guard_sessions"])
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_unhandled_routed_reply_keeps_action_guard_session(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        identity_id = 990302
        now = 1_780_000_000.0
        reply_to_msg_id = 7001
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(identity_id)
            action_guard.note_sent(".元婴出窍", identity_id, reply_to_msg_id, sent_at=now - 10)

            event = SimpleNamespace(id=7002, chat_id=-1001680975844, sender_id=8325841058)
            reply_to = SimpleNamespace(id=reply_to_msg_id, raw_text=".元婴出窍", sender_id=identity_id)
            reply_context = {
                "send_as_id": identity_id,
                "family": "yuanying",
                "reply_to_msg_id": reply_to_msg_id,
                "root_msg_id": reply_to_msg_id,
                "reply_to_sender_id": identity_id,
            }

            with patch.object(app, "schedule_cleanup", new_callable=AsyncMock), \
                    patch.object(app, "record_unhandled_routed_reply", return_value=True) as unhandled_mock:
                handled = asyncio.run(app._handle_routed_reply_event(
                    event,
                    "这是一条和元婴无关的回复文本",
                    now,
                    reply_to,
                    reply_context,
                    allow_tree_panel_claim=False,
                ))

            self.assertFalse(handled)
            self.assertIn("yuanying_launch", action_guard.get_action_guard_sessions(identity_id))
            unhandled_mock.assert_called_once()
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_action_guard_tracks_wendao_inflight_by_reply_state(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        identity_id = 990303
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["wendao_reply_to_msg_id"] = 22027
                state_module.state["wendao_reply_due_at"] = 1_780_000_100.0

                allowed, reason = action_guard.before_send(".问道", send_as_id=identity_id, now=1_780_000_050.0)
                self.assertFalse(allowed)
                self.assertIn("问道", reason)

                allowed, reason = action_guard.before_send(".问道", send_as_id=identity_id, now=1_780_000_200.0)
                self.assertTrue(allowed, reason)
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_action_guard_tracks_explore_rift_inflight_by_reply_state(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        identity_id = 990305
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["explore_rift_reply_to_msg_id"] = 10425942
                state_module.state["explore_rift_reply_due_at"] = 1_780_000_100.0
                state_module.state["explore_rift_pending_result_msg_id"] = 10425944
                state_module.state["next_explore_rift_time"] = 1_780_000_100.0

                allowed, reason = action_guard.before_send(".探寻裂缝", send_as_id=identity_id, now=1_780_000_050.0)
                self.assertFalse(allowed)
                self.assertIn("探寻裂缝", reason)

                state_module.state["explore_rift_reply_due_at"] = 1_780_000_000.0
                allowed, reason = action_guard.before_send(".探寻裂缝", send_as_id=identity_id, now=1_780_000_050.0)
                self.assertFalse(allowed)
                self.assertIn("探寻裂缝", reason)

                state_module.state["explore_rift_pending_result_msg_id"] = 0
                state_module.state["next_explore_rift_time"] = 1_780_000_000.0
                allowed, reason = action_guard.before_send(".探寻裂缝", send_as_id=identity_id, now=1_780_000_050.0)
                self.assertTrue(allowed, reason)
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_action_guard_quiets_ordinary_commands_during_explore_rift_rebirth(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        identity_id = 990306
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["explore_rift_rebirth_required"] = True
                state_module.state["explore_rift_rebirth_phase"] = "requesting"

                for command in (".深度闭关", ".钓鱼 青溪浅滩 灵米饵"):
                    with self.subTest(command=command):
                        allowed, reason = action_guard.before_send(command, send_as_id=identity_id, now=1_780_000_050.0)
                        self.assertFalse(allowed)
                        self.assertIn("普通指令静默", reason)
                        self.assertFalse(action_guard.should_log_block(command, send_as_id=identity_id, now=1_780_000_050.0))

                allowed, reason = action_guard.before_send(".夺舍重生", send_as_id=identity_id, now=1_780_000_050.0)
                self.assertTrue(allowed, reason)
                allowed, reason = action_guard.before_send(".重生 1", send_as_id=identity_id, now=1_780_000_050.0)
                self.assertTrue(allowed, reason)
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_runtime_tracks_explore_rift_pending_result_ids(self):
        meta_snapshot = copy.deepcopy(state_module._meta_state)
        identity_id = 990304
        try:
            state_module._meta_state["identity_ids"] = []
            state_module._meta_state["identity_states"] = {}
            state_module._meta_state["send_as_profiles"] = {}
            state_module.ensure_identity_registered(identity_id)
            with state_module.use_identity(identity_id):
                state_module.state["explore_rift_reply_to_msg_id"] = 10425942
                state_module.state["explore_rift_pending_result_msg_id"] = 10425944
                state_module.state["explore_rift_last_msg_id"] = 10425944

            with state_module.use_identity(identity_id):
                self.assertEqual("explore_rift", runtime._get_special_tracked_message_family(state_module.state, 10425942))
                self.assertEqual("explore_rift", runtime._get_special_tracked_message_family(state_module.state, 10425944))
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(meta_snapshot)

    def test_passive_event_iter_limit_allows_deeper_audit_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "ledger.jsonl"
            with path.open("w", encoding="utf-8") as fp:
                for msg_id in range(1205):
                    fp.write(json.dumps({"kind": "skipped", "msg_id": msg_id}) + "\n")

            events = passive_event_ledger.iter_passive_events(path=str(path), limit=1205)

        self.assertEqual(1205, len(events))
        self.assertEqual(0, events[0]["msg_id"])
        self.assertEqual(1204, events[-1]["msg_id"])


if __name__ == "__main__":
    unittest.main()
