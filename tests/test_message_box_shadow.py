import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("XIUXIAN_TESTING", "1")
os.environ.setdefault("API_ID", "12345")
os.environ.setdefault("API_HASH", "00000000000000000000000000000000")
os.environ.setdefault("TG_PROXY_TYPE", "")
os.environ.setdefault("TG_PROXY_HOST", "127.0.0.1:7890")
os.environ.setdefault("LOG_GROUP_ID", "0")
os.environ.setdefault("LOG_SEND_MODE", "account")
os.environ.setdefault("ADMIN_ID", "1")
os.environ.setdefault("CHAOGU_UI_HOST", "127.0.0.1")
os.environ.setdefault("CHAOGU_UI_PORT", "3030")
sys.path.insert(0, str(PROJECT_ROOT))

from model import app
from model import message_contract
from model.message_box import (
    MessageBox,
    adapt_message_fact_for_legacy_reply,
    build_message_box_snapshot_payload,
    build_message_fact_from_event,
    build_message_fact_from_fixture,
    shadow_compare_verified_event,
    write_message_box_snapshot_payload,
)
from model.real_message_replay import iter_real_message_samples
from model.verified_event import DELIVERY_EDITED, DELIVERY_NEW, from_telegram_event


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "real_message_samples.json"


class MessageBoxShadowTests(unittest.TestCase):
    def test_claimed_prompt_handler_ignores_edited_deliveries(self):
        async def handler(*args, **kwargs):
            return True

        event = SimpleNamespace(id=7001)
        with patch.object(app, "_claim_runtime_event") as claim_mock:
            handled = asyncio.run(
                app._run_claimed_prompt_handler(
                    "jiyin_prompt",
                    handler,
                    "prompt",
                    1234.0,
                    event,
                    event_kind="edit",
                )
            )

        self.assertFalse(handled)
        claim_mock.assert_not_called()

    def test_message_fact_matches_existing_verified_event_contract(self):
        event = SimpleNamespace(id=10140775, chat_id=-1001680975844, sender_id=8325841058)
        reply_to = SimpleNamespace(id=10140774, sender_id=8325841058, raw_text=".侍妾远航 冒险")
        reply_context = {
            "send_as_id": 3504367852,
            "family": "concubine_voyage",
            "reply_to_msg_id": 10140774,
            "root_msg_id": 10140770,
            "reply_to_sender_id": 8325841058,
        }
        text = "【乱星海远航·启】\n预计归航时间：12小时 后"

        fact = build_message_fact_from_event(
            event,
            text,
            reply_context,
            reply_to=reply_to,
            event_type="edit",
            is_game_group=True,
            is_game_bot=True,
        )
        verified = from_telegram_event(event, text, reply_context, event_kind="edit")

        self.assertTrue(all(shadow_compare_verified_event(fact, verified).values()))
        self.assertEqual(verified, fact.to_verified_game_event())
        self.assertTrue(fact.is_game_group)
        self.assertTrue(fact.is_game_bot)
        self.assertTrue(fact.is_edit)
        self.assertEqual(DELIVERY_EDITED, fact.delivery_kind)
        self.assertTrue(fact.is_edited_delivery)
        self.assertFalse(fact.is_new_delivery)
        self.assertEqual(DELIVERY_EDITED, verified.delivery_kind)
        self.assertTrue(verified.is_edited_delivery)
        self.assertFalse(verified.is_new_delivery)
        self.assertEqual(10140774, fact.reply_to_msg_id)
        self.assertEqual(16, len(fact.text_hash))

    def test_message_fact_without_reply_context_still_matches_verified_event_shape(self):
        event = SimpleNamespace(id=9512605, chat_id=-1001680975844, sender_id=8325841058)
        text = "【落云宗 · 灵眼之树】\n7. growrdick (你): 1039"

        fact = build_message_fact_from_event(event, text, {}, event_type="message")
        verified = from_telegram_event(event, text, {}, event_kind="message")

        self.assertTrue(all(shadow_compare_verified_event(fact, verified).values()))
        self.assertEqual("message:reply_context", fact.route_source)
        self.assertEqual(DELIVERY_NEW, fact.delivery_kind)
        self.assertTrue(fact.is_new_delivery)
        self.assertFalse(fact.is_edited_delivery)
        self.assertEqual(DELIVERY_NEW, verified.delivery_kind)
        self.assertTrue(verified.is_new_delivery)
        self.assertFalse(verified.is_edited_delivery)
        self.assertEqual(0, fact.identity_id)
        self.assertEqual("", fact.family)
        self.assertEqual({}, fact.reply_context)

    def test_legacy_reply_adapter_preserves_existing_handler_arguments(self):
        event = SimpleNamespace(id=10380517, chat_id=-1001680975844, sender_id=8325841058)
        reply_to = SimpleNamespace(id=10380515, sender_id=8757550896, raw_text=".探寻裂缝")
        reply_context = {
            "send_as_id": 8757550896,
            "family": "explore_rift",
            "reply_to_msg_id": 10380515,
            "root_msg_id": 10380515,
            "reply_to_sender_id": 8757550896,
        }

        fact = build_message_fact_from_event(
            event,
            "裂缝深处法则乱流渐息，你带回了些许碎片。",
            reply_context,
            reply_to=reply_to,
        )
        adapter = adapt_message_fact_for_legacy_reply(fact, reply_to=reply_to)

        self.assertEqual(fact.raw_text, adapter.text)
        self.assertIs(reply_to, adapter.reply_to)
        self.assertEqual(reply_context, adapter.reply_context)
        self.assertEqual("explore_rift", adapter.matched_family)

    def test_replay_fixtures_can_build_read_only_message_facts(self):
        samples = list(iter_real_message_samples(FIXTURE_PATH))
        self.assertGreaterEqual(len(samples), 80)

        for index, sample in enumerate(samples, start=1):
            with self.subTest(sample_id=sample.sample_id):
                fact = build_message_fact_from_fixture(
                    sample,
                    chat_id=-1001680975844,
                    msg_id=900000 + index,
                    sender_id=8325841058,
                )
                self.assertEqual(sample.event_type, fact.event_type)
                self.assertEqual(sample.text, fact.raw_text)
                self.assertEqual(sample.family, fact.family)
                self.assertEqual(sample.source, fact.source)
                self.assertEqual(16, len(fact.text_hash))
                self.assertFalse(fact.is_game_group)
                self.assertFalse(fact.is_game_bot)

    def test_message_box_upsert_dedupes_and_scans_after_cursor(self):
        box = MessageBox(chat_id=-1001680975844, cap=10)
        first = build_message_fact_from_event(
            SimpleNamespace(id=1001, chat_id=-1001680975844, sender_id=8325841058),
            "first",
            {"family": "tower"},
        )
        second = build_message_fact_from_event(
            SimpleNamespace(id=1002, chat_id=-1001680975844, sender_id=8325841058),
            "second",
            {"family": "tower"},
        )

        self.assertTrue(box.upsert(first))
        self.assertFalse(box.upsert(first))
        self.assertTrue(box.upsert(second))

        self.assertEqual(2, len(box))
        self.assertEqual(1002, box.head())
        self.assertEqual(2, box.head_seq())
        self.assertEqual([1002], [fact.msg_id for fact in box.scan_after(1001)])
        self.assertEqual([1002], [fact.msg_id for fact in box.scan_after_seq(1)])

    def test_message_box_replaces_same_msg_id_when_text_changes(self):
        box = MessageBox(chat_id=-1001680975844, cap=10)
        event = SimpleNamespace(id=1001, chat_id=-1001680975844, sender_id=8325841058)
        original = build_message_fact_from_event(event, "original", {"family": "tower"})
        edited = build_message_fact_from_event(event, "edited", {"family": "tower"}, event_type="edit")

        self.assertTrue(box.upsert(original))
        self.assertTrue(box.upsert(edited))

        self.assertEqual(1, len(box))
        self.assertEqual("edited", box.get(1001).raw_text)
        self.assertEqual("edit", box.get(1001).event_type)
        self.assertEqual(2, box.get(1001).ingest_seq)

    def test_message_box_late_original_message_does_not_revert_edit(self):
        box = MessageBox(chat_id=-1001680975844, cap=10)
        event = SimpleNamespace(id=1001, chat_id=-1001680975844, sender_id=8325841058)
        original = build_message_fact_from_event(event, "original", {"family": "tower"})
        edited = build_message_fact_from_event(event, "edited", {"family": "tower"}, event_type="edit")

        self.assertTrue(box.upsert(edited))
        self.assertFalse(box.upsert(original))

        self.assertEqual(1, len(box))
        self.assertEqual("edited", box.get(1001).raw_text)
        self.assertEqual("edit", box.get(1001).event_type)

    def test_seq_cursor_can_see_edit_redelivery_without_default_consuming_it(self):
        box = MessageBox(chat_id=-1001680975844, cap=10)
        event = SimpleNamespace(id=1001, chat_id=-1001680975844, sender_id=8325841058)
        original = build_message_fact_from_event(event, "天机罗盘缓缓转动", {"family": "divination"})
        edited = build_message_fact_from_event(event, "【神物现世】请回复 .换取", {"family": "divination"}, event_type="edit")

        self.assertTrue(box.upsert(original))
        cursor_msg_id = box.head()
        cursor_seq = box.head_seq()
        self.assertTrue(box.upsert(edited))

        self.assertEqual([], [fact.msg_id for fact in box.scan_after(cursor_msg_id)])
        self.assertEqual([], [fact.msg_id for fact in box.scan_after_seq(cursor_seq)])
        self.assertEqual([1001], [fact.msg_id for fact in box.scan_after_seq(cursor_seq, include_edits=True)])

    def test_message_box_snapshot_is_read_only_and_stable(self):
        box = MessageBox(chat_id=-1001680975844, cap=10)
        first = build_message_fact_from_event(
            SimpleNamespace(id=1001, chat_id=-1001680975844, sender_id=8325841058),
            "first",
            {"family": "tower"},
        )
        second = build_message_fact_from_event(
            SimpleNamespace(id=1002, chat_id=-1001680975844, sender_id=8325841058),
            "second",
            {"family": "tower"},
        )

        box.upsert(first)
        snap = box.snapshot()
        box.upsert(second)

        self.assertEqual(1, len(snap))
        self.assertEqual([1001], [fact.msg_id for fact in snap.scan_after(None)])
        self.assertEqual([1001], [fact.msg_id for fact in snap.scan_after_seq(None)])
        self.assertEqual(2, len(box))
        self.assertEqual([1001, 1002], [fact.msg_id for fact in box.scan_after(None)])

    def test_message_box_cap_evicts_oldest_delivery_and_latest_fact(self):
        box = MessageBox(chat_id=-1001680975844, cap=2)
        for msg_id in (1001, 1002, 1003):
            fact = build_message_fact_from_event(
                SimpleNamespace(id=msg_id, chat_id=-1001680975844, sender_id=8325841058),
                f"msg {msg_id}",
                {"family": "tower", "send_as_id": msg_id},
            )
            self.assertTrue(box.upsert(fact))

        self.assertEqual(1001, box.last_evicted_msg_id)
        self.assertEqual(1, box.last_evicted_seq)
        self.assertIsNone(box.get(1001))
        self.assertEqual([1002, 1003], [fact.msg_id for fact in box.scan_after(None)])
        self.assertEqual([1003], [fact.identity_id for fact in box.scan_after(1002)])

    def test_message_box_rejects_other_chat_when_chat_is_scoped(self):
        box = MessageBox(chat_id=-1001680975844, cap=10)
        fact = build_message_fact_from_event(
            SimpleNamespace(id=1001, chat_id=-100999, sender_id=8325841058),
            "wrong chat",
            {"family": "tower"},
        )

        self.assertFalse(box.upsert(fact))
        self.assertTrue(box.is_empty())

    def test_message_box_snapshot_payload_is_report_compatible_json(self):
        box = MessageBox(chat_id=-1001680975844, cap=10)
        fact = build_message_fact_from_event(
            SimpleNamespace(id=1001, chat_id=-1001680975844, sender_id=8325841058),
            "【乱星海远航·归】",
            {
                "send_as_id": 3504367852,
                "family": "concubine_voyage",
                "reply_to_msg_id": 1000,
                "root_msg_id": 1000,
            },
            is_game_group=True,
            is_game_bot=True,
        )
        box.upsert(fact)

        payload = build_message_box_snapshot_payload(box, now=1234.5)

        self.assertEqual("xiuxian.message_box.shadow.v1", payload["schema"])
        self.assertEqual(1234.5, payload["created_at"])
        self.assertEqual(1, payload["fact_count"])
        self.assertEqual(1001, payload["head_msg_id"])
        self.assertEqual(1, payload["head_seq"])
        self.assertEqual("concubine_voyage", payload["facts"][0]["family"])
        self.assertEqual(DELIVERY_NEW, payload["facts"][0]["delivery_kind"])
        self.assertEqual(3504367852, payload["facts"][0]["identity_id"])
        self.assertEqual("【乱星海远航·归】", payload["facts"][0]["raw_text"])

    def test_message_box_snapshot_payload_limit_keeps_latest_deliveries(self):
        box = MessageBox(chat_id=-1001680975844, cap=10)
        for msg_id in (1001, 1002, 1003):
            box.upsert(
                build_message_fact_from_event(
                    SimpleNamespace(id=msg_id, chat_id=-1001680975844, sender_id=8325841058),
                    f"msg {msg_id}",
                    {"send_as_id": 3504367852, "family": "tower", "reply_to_msg_id": 1000},
                )
            )

        payload = build_message_box_snapshot_payload(box, limit=2)

        self.assertEqual(2, payload["fact_count"])
        self.assertEqual([1002, 1003], [item["msg_id"] for item in payload["facts"]])

    def test_write_message_box_snapshot_payload_writes_json_atomically(self):
        payload = {
            "schema": "xiuxian.message_box.shadow.v1",
            "created_at": 1234.5,
            "facts": [],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "shadow.json"
            written = write_message_box_snapshot_payload(path, payload)

            self.assertEqual(str(path), written)
            loaded = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(payload, loaded)


class AppMessageBoxShadowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app._reset_message_box_shadow_for_test()

    async def asyncTearDown(self):
        app._reset_message_box_shadow_for_test()

    def test_app_shadow_recording_is_snapshot_only(self):
        event = SimpleNamespace(id=10140775, chat_id=-1001680975844, sender_id=8325841058)
        reply_to = SimpleNamespace(id=10140774, sender_id=3504367852, raw_text=".侍妾远航 冒险")
        reply_context = {
            "send_as_id": 3504367852,
            "family": "concubine_voyage",
            "reply_to_msg_id": 10140774,
            "root_msg_id": 10140770,
            "reply_to_sender_id": 3504367852,
        }

        with patch.object(app, "send_game_command", new=AsyncMock()) as send_mock:
            fact = app._record_message_box_shadow(
                event,
                "【乱星海远航·启】\n预计归航时间：12小时 后",
                reply_context,
                reply_to=reply_to,
                event_type="message",
                is_game_bot=True,
                is_game_group=True,
            )

        self.assertIsNotNone(fact)
        send_mock.assert_not_awaited()
        snapshot = app._get_message_box_shadow_snapshot()
        self.assertEqual(1, len(snapshot))
        stored = snapshot.get(10140775)
        self.assertEqual("concubine_voyage", stored.family)
        self.assertEqual(3504367852, stored.identity_id)
        self.assertTrue(stored.is_game_bot)
        self.assertTrue(stored.is_game_group)
        self.assertEqual("telegram_shadow", stored.source)

    def test_app_shadow_payload_and_write_export_current_snapshot(self):
        event = SimpleNamespace(id=10140775, chat_id=-1001680975844, sender_id=8325841058)
        app._record_message_box_shadow(
            event,
            "【乱星海远航·归】",
            {"send_as_id": 3504367852, "family": "concubine_voyage", "reply_to_msg_id": 10140774},
            event_type="message",
            is_game_bot=True,
            is_game_group=True,
        )

        payload = app.get_message_box_shadow_payload(now=1234.5)
        self.assertEqual(1, payload["fact_count"])
        self.assertEqual("concubine_voyage", payload["facts"][0]["family"])

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "shadow.json"
            written = app.write_message_box_shadow_snapshot(path, now=1234.5)
            loaded = json.loads(Path(written).read_text(encoding="utf-8"))

        self.assertEqual(payload, loaded)

    def test_app_registers_shadow_provider_for_control_layer(self):
        from model import control

        self.assertIs(control._message_box_shadow_payload_provider, app.get_message_box_shadow_payload)

    async def test_suspected_game_bot_shadow_failure_does_not_block_routing(self):
        event = SimpleNamespace(id=9452531, sender_id=888001, chat_id=-1001680975844)
        reply_to = SimpleNamespace(id=7001, sender_id=3504367852, raw_text=".卜筮问天")
        reply_context = {
            "send_as_id": 3504367852,
            "family": "divination",
            "reply_to_msg_id": 7001,
            "root_msg_id": 7001,
            "reply_to_sender_id": 3504367852,
        }

        with patch.object(app, "_resolve_identity_sender_id", return_value=0), \
                patch.object(app, "_resolve_event_reply", new=AsyncMock(return_value=(reply_to, reply_context))), \
                patch.object(app, "_looks_like_game_bot_reply", return_value=True), \
                patch.object(app._message_box_shadow, "upsert", side_effect=RuntimeError("shadow down")), \
                patch.object(app, "_handle_routed_reply_event", new=AsyncMock(return_value=True)) as routed_mock, \
                patch.object(app, "_note_game_bot_activity", new=AsyncMock()), \
                patch.object(app, "_record_suspected_game_bot", new=AsyncMock()), \
                patch.object(app, "send_game_command", new=AsyncMock()) as send_mock:
            handled = await app._handle_suspected_game_bot_reply(
                event,
                "【神物现世】请回复 .换取 凝血草",
                1000.0,
                edited=True,
            )

        self.assertTrue(handled)
        self.assertEqual("edit", routed_mock.await_args.kwargs["event_kind"])
        send_mock.assert_not_awaited()
        self.assertTrue(app._get_message_box_shadow_snapshot().is_empty())


class MessageBoxShadowAlignmentTests(unittest.TestCase):
    def _fact(self, msg_id, family, text="ok", *, event_type="message", identity_id=3504367852):
        return build_message_fact_from_event(
            SimpleNamespace(id=msg_id, chat_id=-1001680975844, sender_id=8325841058),
            text,
            {
                "send_as_id": identity_id,
                "family": family,
                "reply_to_msg_id": msg_id - 1,
                "root_msg_id": msg_id - 1,
                "reply_to_sender_id": identity_id,
            },
            event_type=event_type,
            is_game_group=True,
            is_game_bot=True,
        )

    def test_shadow_alignment_compares_routeable_facts_with_passive_ledger_events(self):
        box = MessageBox(cap=10)
        box.upsert(self._fact(1001, "concubine_voyage", "【乱星海远航·归】"))
        box.upsert(self._fact(1002, "divination", "【神物现世】"))
        box.upsert(self._fact(1003, "explore_rift", "裂缝深处法则乱流渐息"))

        events = [
            {
                "kind": "changed",
                "identity_id": 3504367852,
                "family": "concubine_voyage",
                "msg_id": 1001,
                "source_message_id": 1001,
                "decision": "state_changed",
            },
            {
                "kind": "skipped",
                "identity_id": 3504367852,
                "family": "divination",
                "msg_id": 1002,
                "source_message_id": 1002,
                "reason": "unhandled_routed_reply",
                "decision": "handler_not_matched",
            },
        ]

        summary = message_contract.summarize_message_box_shadow_alignment(box, events, latest_limit=5)

        self.assertEqual(3, summary["observed_total"])
        self.assertEqual(3, summary["routeable_total"])
        self.assertEqual(2, summary["matched_total"])
        self.assertEqual(1, summary["changed_total"])
        self.assertEqual(1, summary["unhandled_total"])
        self.assertEqual(1, summary["missing_total"])
        self.assertEqual(
            {
                "changed": 1,
                "missing": 1,
                "unhandled": 1,
            },
            summary["by_status"],
        )
        self.assertEqual("explore_rift", summary["latest_missing"][0]["family"])
        self.assertEqual(1003, summary["latest_missing"][0]["msg_id"])

    def test_shadow_alignment_ignores_unrouteable_broadcasts_for_missing_count(self):
        box = MessageBox(cap=10)
        box.upsert(
            build_message_fact_from_event(
                SimpleNamespace(id=2001, chat_id=-1001680975844, sender_id=8325841058),
                "【真仙试锋】战场风云变幻",
                {},
                is_game_group=True,
                is_game_bot=True,
            )
        )

        summary = message_contract.summarize_message_box_shadow_alignment(box, [])

        self.assertEqual(1, summary["observed_total"])
        self.assertEqual(0, summary["routeable_total"])
        self.assertEqual(0, summary["missing_total"])
        self.assertEqual({}, summary["by_status"])

    def test_shadow_alignment_includes_edits_by_default(self):
        box = MessageBox(cap=10)
        box.upsert(self._fact(3001, "divination", "天机罗盘缓缓转动"))
        box.upsert(self._fact(3001, "divination", "【神物现世】请回复 .换取", event_type="edit"))

        summary = message_contract.summarize_message_box_shadow_alignment(box, [])

        self.assertEqual(2, summary["observed_total"])
        self.assertEqual(1, summary["routeable_total"])
        self.assertEqual(1, summary["missing_total"])
        self.assertEqual("edit", summary["latest_missing"][0]["event_type"])


if __name__ == "__main__":
    unittest.main()
