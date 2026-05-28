import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.config import CD_BUFFER_SEC, TAIYI_CYCLE_CD_SEC
from model.features import deep_retreat, storage_bag, taiyi
from model.features.storage_bag import handle_storage_bag_transfer_reply, start_storage_bag_transfer_task


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "real_message_samples.json"


def real_text(sample_id):
    with FIXTURE_PATH.open("r", encoding="utf-8") as fp:
        samples = json.load(fp)
    return samples[sample_id]["text"]


class RealMessageReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        storage_bag._clear_storage_bag_transfer_state()

    async def asyncTearDown(self):
        await storage_bag.cancel_storage_bag_transfer_task()
        storage_bag._clear_storage_bag_transfer_state()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        await super().asyncTearDown()

    def _prepare_identity(self, send_as_id, username):
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username=username)

    async def test_deep_retreat_real_summary_finalizes_wait(self):
        send_as_id = 3870643893
        now = 1_779_916_182.0
        self._prepare_identity(send_as_id, "jihejish")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - 10
            state_module.state["last_deep_retreat_summary_msg_id"] = 9491712

        with (
            patch.object(deep_retreat, "console_log"),
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()),
            patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
            patch.object(deep_retreat, "save_state"),
        ):
            await deep_retreat.handle_deep_retreat_summary_broadcast(
                real_text("deep_retreat.tagged_summary.jihejish"),
                now,
            )

        with state_module.use_identity(send_as_id):
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
        self.assertTrue(any(
            call.kwargs.get("family") == "deep_retreat"
            and call.kwargs.get("decision") == "summary_finalized"
            and "@jihejish" in str(call.kwargs.get("matched_text") or "")
            for call in inbox_mock.call_args_list
        ))

    async def test_deep_retreat_real_non_summary_is_ignored(self):
        send_as_id = 3870643893
        now = 1_779_916_190.0
        self._prepare_identity(send_as_id, "jihejish")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - 10

        with (
            patch.object(deep_retreat, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
        ):
            await deep_retreat.handle_deep_retreat_summary_broadcast(
                real_text("deep_retreat.non_summary.wild_training"),
                now,
            )

        with state_module.use_identity(send_as_id):
            self.assertEqual("waiting_summary", state_module.state["deep_retreat_phase"])
        audit_mock.assert_not_awaited()
        inbox_mock.assert_not_called()

    async def test_taiyi_real_yindao_success_calibrates_cycle(self):
        send_as_id = 8659059191
        now = 1_779_911_737.0
        self._prepare_identity(send_as_id, "WalterWA2000")
        reply_to = SimpleNamespace(id=9446793, sender_id=send_as_id, raw_text=".引道 水")

        with state_module.use_identity(send_as_id):
            state_module.state["taiyi_enabled"] = True
            state_module.state["taiyi_node_search_enabled"] = False
            state_module.state["taiyi_phase"] = "idle"
            state_module.state["next_taiyi_cycle_time"] = now - 1
            with (
                patch.object(taiyi, "send_audit_log", new=AsyncMock()),
                patch.object(taiyi, "_fire_and_forget") as fire_mock,
                patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
                patch.object(taiyi, "save_state"),
            ):
                handled = await taiyi.handle_taiyi_yindao_reply(
                    real_text("taiyi.yindao.success_water"),
                    now,
                    reply_to,
                    matched_family="taiyi_yindao",
                )

        self.assertTrue(handled)
        fire_mock.assert_not_called()
        self.assertTrue(any(
            call.kwargs.get("family") == "taiyi_yindao"
            and call.kwargs.get("decision") == "calibrate_manual_late_no_search"
            and "你引动【水之道】" in str(call.kwargs.get("matched_text") or "")
            for call in inbox_mock.call_args_list
        ))
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["taiyi_phase"])
            self.assertEqual(now + TAIYI_CYCLE_CD_SEC + CD_BUFFER_SEC, state_module.state["next_taiyi_cycle_time"])

    async def test_storage_bag_real_manual_listing_replaces_original_and_syncs_inventory(self):
        source_id = 1001
        target_id = 1002
        self._prepare_identity(source_id, "source")
        self._prepare_identity(target_id, "target")
        state_module.set_storage_bag_records({
            str(source_id): {"updated_at": 1000, "items": {"筑基丹": 5}, "sections": {"法宝/丹药/杂物": {"筑基丹": 5}}},
            str(target_id): {"updated_at": 1000, "items": {"凝血草": 2}, "sections": {"材料": {"凝血草": 2}}},
        })

        sent = []

        async def fake_send(command, **kwargs):
            msg = SimpleNamespace(id=700 + len(sent), sent_at=1_779_916_200.0 + len(sent))
            sent.append((command, kwargs, msg))
            return msg

        with (
            patch("model.features.storage_bag.send_game_command", side_effect=fake_send),
            patch("model.features.storage_bag.send_audit_log", new=AsyncMock()),
            patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
        ):
            ok, message, _transfer = await start_storage_bag_transfer_task(
                source_id,
                target_id,
                [{"item_name": "筑基丹", "quantity": 1, "method": "basic"}],
                "凝血草",
            )
            self.assertTrue(ok, message)

            listing_msg = sent[0][2]
            manual_listing_msg_id = listing_msg.id + 99
            listing_handled = await handle_storage_bag_transfer_reply(
                real_text("storage_bag.listing_success.manual_22028"),
                1_779_916_210.0,
                SimpleNamespace(id=manual_listing_msg_id, raw_text=sent[0][0]),
                matched_family="storage_bag_listing",
                reply_context={"send_as_id": target_id, "family": "storage_bag_listing", "reply_to_msg_id": manual_listing_msg_id},
            )
            self.assertTrue(listing_handled)
            self.assertEqual(".购买 22028", sent[-1][0])

            original_listing_ignored = await handle_storage_bag_transfer_reply(
                real_text("storage_bag.listing_success.original_22027"),
                1_779_916_215.0,
                SimpleNamespace(id=listing_msg.id, raw_text=sent[0][0]),
                matched_family="storage_bag_listing",
                reply_context={"send_as_id": target_id, "family": "storage_bag_listing", "reply_to_msg_id": listing_msg.id},
            )
            self.assertFalse(original_listing_ignored)
            self.assertEqual(".购买 22028", sent[-1][0])

            buy_msg = sent[-1][2]
            buy_handled = await handle_storage_bag_transfer_reply(
                real_text("storage_bag.buy_success.basic"),
                1_779_916_220.0,
                SimpleNamespace(id=buy_msg.id, raw_text=sent[-1][0]),
                matched_family="storage_bag_buy",
                reply_context={"send_as_id": source_id, "family": "storage_bag_buy", "reply_to_msg_id": buy_msg.id},
            )

        self.assertTrue(buy_handled)
        self.assertTrue(any(
            call.kwargs.get("family") == "storage_bag_buy"
            and call.kwargs.get("decision") == "buy_success_inventory_synced"
            and "交易成功" in str(call.kwargs.get("matched_text") or "")
            for call in inbox_mock.call_args_list
        ))
        self.assertTrue(any(
            call.kwargs.get("family") == "storage_bag_listing"
            and call.kwargs.get("decision") == "stale_listing_reply_ignored"
            and "挂单ID: 22027" in str(call.kwargs.get("matched_text") or "")
            for call in inbox_mock.call_args_list
        ))
        records = state_module.get_storage_bag_records()
        self.assertEqual(4, records[str(source_id)]["items"]["筑基丹"])
        self.assertEqual(1, records[str(source_id)]["items"]["凝血草"])
        self.assertEqual(1, records[str(target_id)]["items"]["筑基丹"])
        self.assertEqual(1, records[str(target_id)]["items"]["凝血草"])


if __name__ == "__main__":
    unittest.main()
