import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.config import CD_BUFFER_SEC, TAIYI_CYCLE_CD_SEC
from model.features import deep_retreat, explore_rift, small_world, storage_bag, taiyi, wendao, workflow_log
from model.features.storage_bag import handle_storage_bag_transfer_reply, start_storage_bag_transfer_task
from model.real_message_replay import get_real_message_text, iter_real_message_samples


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "real_message_samples.json"


def real_text(sample_id):
    return get_real_message_text(FIXTURE_PATH, sample_id)


def _read_workflow_events(tmpdir):
    events = []
    for path in Path(tmpdir).glob("**/*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


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

    def test_replay_fixture_can_filter_by_module_and_family(self):
        storage_samples = list(iter_real_message_samples(FIXTURE_PATH, module="storage_bag"))
        listing_samples = list(iter_real_message_samples(FIXTURE_PATH, family="storage_bag_listing"))
        dungeon_samples = list(iter_real_message_samples(FIXTURE_PATH, module="join_dungeon"))
        yuanying_samples = list(iter_real_message_samples(FIXTURE_PATH, family="yuanying"))

        self.assertGreaterEqual(len(storage_samples), 3)
        self.assertEqual(2, len(listing_samples))
        self.assertEqual(2, len(dungeon_samples))
        self.assertEqual(1, len(yuanying_samples))
        self.assertTrue(all(sample.text for sample in storage_samples))
        self.assertTrue(any("副本ID" in sample.text for sample in dungeon_samples))
        self.assertIn("元神归窍总结", yuanying_samples[0].text)

    async def test_storage_bag_real_panel_updates_inventory_cache(self):
        send_as_id = 8373721506
        now = 1_781_452_367.0
        self._prepare_identity(send_as_id, "zedwang125")
        state_module.set_storage_bag_records({})

        with patch.object(storage_bag, "save_state"):
            handled = await storage_bag.handle_storage_bag_reply(
                real_text("storage_bag.panel.zedwang"),
                now,
                matched_family="storage_bag",
            )

        self.assertTrue(handled)
        records = state_module.get_storage_bag_records()
        record = records[str(send_as_id)]
        self.assertEqual("@zedwang125", record["owner_username"])
        self.assertEqual(2195, record["items"]["灵石"])
        self.assertEqual(319, record["sections"]["材料"]["凝血草"])
        self.assertEqual(1, record["sections"]["法宝/丹药/杂物"]["真仙试锋"])

    async def test_small_world_real_refine_reply_clears_pending_and_rechecks(self):
        send_as_id = 5231593703
        now = 1_781_453_889.0
        self._prepare_identity(send_as_id, "smallworld")
        reply_to = SimpleNamespace(id=10378360, sender_id=send_as_id, raw_text=".神识淬炼 7550")

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_refine_enabled"] = True
            state_module.state["small_world_phase"] = "refine_sent"
            state_module.state["small_world_refine_msg_id"] = 10378360
            state_module.state["small_world_incense_stock"] = 8000

            with (
                patch.object(small_world, "_send_query", new=AsyncMock(return_value=True)) as query_mock,
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_refine_reply(
                    real_text("small_world.refine.success"),
                    now,
                    reply_to,
                    matched_family="small_world_refine",
                )

            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_refine_msg_id"])
            self.assertEqual(450, state_module.state["small_world_incense_stock"])
            query_mock.assert_awaited_once_with(now, "淬炼后复查")

    async def test_small_world_real_relief_reply_updates_local_snapshot(self):
        send_as_id = 5231593703
        now = 1_781_400_508.0
        self._prepare_identity(send_as_id, "smallworld")

        with state_module.use_identity(send_as_id):
            state_module.state["small_world_enabled"] = True
            state_module.state["small_world_preach_enabled"] = True
            state_module.state["small_world_phase"] = "preach_pending"
            state_module.state["small_world_preach_reply_to_msg_id"] = 10333591
            state_module.state["small_world_preach_due_at"] = now + 30
            state_module.state["small_world_panel_snapshot"] = {
                "population": 188722,
                "capacity": 190000,
                "faith": 50,
                "faith_max": 100,
                "stability": 70,
                "stability_max": 100,
            }

            with (
                patch.object(small_world.random, "uniform", return_value=60),
                patch.object(small_world, "save_state"),
            ):
                handled = await small_world.handle_small_world_preach_reply(
                    real_text("small_world.relief.success"),
                    now,
                    reply_to=SimpleNamespace(id=10333591, raw_text=".神迹 赈灾"),
                    matched_family="small_world_relief",
                )

            self.assertTrue(handled)
            snapshot = state_module.state["small_world_panel_snapshot"]
            self.assertEqual(190000, snapshot["population"])
            self.assertEqual(72, snapshot["faith"])
            self.assertEqual(100, snapshot["stability"])
            self.assertEqual("idle", state_module.state["small_world_phase"])
            self.assertEqual(0, state_module.state["small_world_preach_reply_to_msg_id"])
            self.assertEqual("preach", state_module.state["small_world_pending_god_action"])

    async def test_wendao_real_result_updates_storage_bag_and_default_cd(self):
        send_as_id = 8757550896
        now = 1_781_115_788.0
        self._prepare_identity(send_as_id, "wendaoer")
        state_module.set_storage_bag_records({})

        with state_module.use_identity(send_as_id):
            state_module.state["wendao_enabled"] = True
            state_module.state["wendao_reply_to_msg_id"] = 10166450
            state_module.state["wendao_reply_due_at"] = now + 30

            with (
                patch.object(wendao.random, "uniform", return_value=0),
                patch.object(wendao, "save_state"),
                patch.object(storage_bag, "save_state"),
                patch.object(wendao, "send_audit_log", new=AsyncMock()),
            ):
                handled = await wendao.handle_wendao_reply(
                    real_text("wendao.result.basic"),
                    now,
                    reply_to=SimpleNamespace(id=10166450, raw_text=".问道"),
                    matched_family="wendao",
                    result_msg_id=10166451,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["wendao_reply_to_msg_id"])
            self.assertEqual(now + wendao.WENDAO_CD, state_module.state["next_wendao_time"])
            self.assertEqual("修为 +1312 ｜ 奖励：一阶妖丹x8、灵石x147、二级妖丹x2", state_module.state["wendao_last_result"])
            records = state_module.get_storage_bag_records()
            self.assertEqual(8, records[str(send_as_id)]["items"]["一阶妖丹"])
            self.assertEqual(147, records[str(send_as_id)]["items"]["灵石"])
            self.assertEqual(2, records[str(send_as_id)]["items"]["二级妖丹"])

    async def test_explore_rift_real_result_updates_storage_bag_and_default_cd(self):
        send_as_id = 8757550896
        now = 1_781_115_788.0
        self._prepare_identity(send_as_id, "riftseeker")
        state_module.set_storage_bag_records({})

        with state_module.use_identity(send_as_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 10380515
            state_module.state["explore_rift_reply_due_at"] = now + 30

            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(storage_bag, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    real_text("explore_rift.success.tianxing"),
                    now,
                    reply_to=SimpleNamespace(id=10380515, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=10380517,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])
            self.assertEqual(
                "奖励：法则碎片·木x1、法则碎片·雷x1、法则碎片·土x1",
                state_module.state["explore_rift_last_result"],
            )
            records = state_module.get_storage_bag_records()
            self.assertEqual(1, records[str(send_as_id)]["items"]["法则碎片·木"])
            self.assertEqual(1, records[str(send_as_id)]["items"]["法则碎片·雷"])
            self.assertEqual(1, records[str(send_as_id)]["items"]["法则碎片·土"])

    async def test_explore_rift_real_terminal_failures_clear_pending_and_default_cd(self):
        send_as_id = 8757550896
        now = 1_781_115_788.0
        self._prepare_identity(send_as_id, "riftseeker")

        for sample_id, reply_to_msg_id, result_msg_id, expected_title in (
            ("explore_rift.failure.storm", 10425942, 10425944, "遭遇风暴"),
            ("explore_rift.failure.beast_defeat", 10426277, 10426278, "不敌败退"),
        ):
            with self.subTest(sample_id=sample_id):
                with state_module.use_identity(send_as_id):
                    state_module.state["explore_rift_enabled"] = True
                    state_module.state["explore_rift_reply_to_msg_id"] = reply_to_msg_id
                    state_module.state["explore_rift_reply_due_at"] = now + 30
                    state_module.state["explore_rift_pending_result_msg_id"] = result_msg_id

                    with (
                        patch.object(explore_rift.random, "uniform", return_value=0),
                        patch.object(explore_rift, "save_state"),
                        patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
                    ):
                        handled = await explore_rift.handle_explore_rift_reply(
                            real_text(sample_id),
                            now,
                            reply_to=SimpleNamespace(id=reply_to_msg_id, raw_text=".探寻裂缝"),
                            matched_family="explore_rift",
                            result_msg_id=result_msg_id,
                        )

                    self.assertTrue(handled)
                    self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
                    self.assertEqual(0, state_module.state["explore_rift_reply_due_at"])
                    self.assertEqual(0, state_module.state["explore_rift_pending_result_msg_id"])
                    self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])
                    self.assertIn(expected_title, state_module.state["explore_rift_last_result"])

    async def test_explore_rift_real_beast_victory_updates_storage_bag_and_default_cd(self):
        send_as_id = 8757550896
        now = 1_781_115_788.0
        self._prepare_identity(send_as_id, "riftseeker")
        state_module.set_storage_bag_records({})

        with state_module.use_identity(send_as_id):
            state_module.state["explore_rift_enabled"] = True
            state_module.state["explore_rift_reply_to_msg_id"] = 10410001
            state_module.state["explore_rift_reply_due_at"] = now + 30
            state_module.state["explore_rift_pending_result_msg_id"] = 10410003

            with (
                patch.object(explore_rift.random, "uniform", return_value=0),
                patch.object(explore_rift, "save_state"),
                patch.object(storage_bag, "save_state"),
                patch.object(explore_rift, "send_audit_log", new=AsyncMock()),
            ):
                handled = await explore_rift.handle_explore_rift_reply(
                    real_text("explore_rift.beast_victory.space_core"),
                    now,
                    reply_to=SimpleNamespace(id=10410001, raw_text=".探寻裂缝"),
                    matched_family="explore_rift",
                    result_msg_id=10410003,
                )

            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["explore_rift_reply_to_msg_id"])
            self.assertEqual(0, state_module.state["explore_rift_reply_due_at"])
            self.assertEqual(0, state_module.state["explore_rift_pending_result_msg_id"])
            self.assertEqual(now + explore_rift.EXPLORE_RIFT_CD, state_module.state["next_explore_rift_time"])
            self.assertEqual(
                "奖励：法则碎片·空间x1、四级妖丹x5、空间之核x1",
                state_module.state["explore_rift_last_result"],
            )
            records = state_module.get_storage_bag_records()
            self.assertEqual(1, records[str(send_as_id)]["items"]["法则碎片·空间"])
            self.assertEqual(5, records[str(send_as_id)]["items"]["四级妖丹"])
            self.assertEqual(1, records[str(send_as_id)]["items"]["空间之核"])

    async def test_deep_retreat_real_summary_finalizes_wait(self):
        send_as_id = 3870643893
        now = 1_779_916_182.0
        self._prepare_identity(send_as_id, "jihejish")

        with state_module.use_identity(send_as_id):
            state_module.state["deep_retreat_enabled"] = True
            state_module.state["deep_retreat_phase"] = "waiting_summary"
            state_module.state["deep_retreat_summary_sent_at"] = now - 10
            state_module.state["last_deep_retreat_summary_msg_id"] = 9491712

        with tempfile.TemporaryDirectory() as tmpdir:
            with (
                patch.object(workflow_log, "WORKFLOW_LOG_DIR", tmpdir),
                patch.object(deep_retreat, "console_log"),
                patch.object(deep_retreat, "send_audit_log", new=AsyncMock()),
                patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
                patch.object(deep_retreat, "save_state"),
            ):
                await deep_retreat.handle_deep_retreat_summary_broadcast(
                    real_text("deep_retreat.tagged_summary.jihejish"),
                    now,
                )
                workflow_events = _read_workflow_events(tmpdir)

        with state_module.use_identity(send_as_id):
            self.assertEqual("post_summary_wait", state_module.state["deep_retreat_phase"])
        self.assertTrue(any(
            call.kwargs.get("family") == "deep_retreat"
            and call.kwargs.get("decision") == "summary_finalized"
            and "@jihejish" in str(call.kwargs.get("matched_text") or "")
            for call in inbox_mock.call_args_list
        ))
        self.assertTrue(any(
            event.get("workflow") == "deep_retreat"
            and event.get("decision") == "summary_finalized"
            and event.get("identity_id") == send_as_id
            and "@jihejish" in event.get("text", "")
            for event in workflow_events
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
            with tempfile.TemporaryDirectory() as tmpdir:
                with (
                    patch.object(workflow_log, "WORKFLOW_LOG_DIR", tmpdir),
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
                    workflow_events = _read_workflow_events(tmpdir)

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
        self.assertTrue(any(
            event.get("workflow") == "taiyi"
            and event.get("decision") == "calibrate_manual_late_no_search"
            and event.get("family") == "taiyi_yindao"
            and event.get("reply_to_msg_id") == 9446793
            and "你引动【水之道】" in event.get("text", "")
            for event in workflow_events
        ))

    async def test_taiyi_real_node_search_success_enters_define_pending(self):
        send_as_id = 8659059191
        now = 1_780_709_397.0
        self._prepare_identity(send_as_id, "WalterWA2000")
        reply_to = SimpleNamespace(id=9918348, sender_id=send_as_id, raw_text=".搜寻节点")

        def close_delayed_define(coro):
            coro.close()

        with state_module.use_identity(send_as_id):
            state_module.state["taiyi_enabled"] = True
            state_module.state["taiyi_node_search_enabled"] = True
            state_module.state["taiyi_phase"] = "search_pending"
            state_module.state["taiyi_node_search_msg_id"] = 9918348

            with (
                patch.object(taiyi, "_fire_and_forget", side_effect=close_delayed_define) as fire_mock,
                patch.object(taiyi, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(taiyi, "save_state"),
            ):
                handled = await taiyi.handle_taiyi_node_search_reply(
                    real_text("taiyi.node_search.success"),
                    now,
                    reply_to,
                    matched_family="taiyi_node_search",
                )

            self.assertTrue(handled)
            self.assertEqual("define_pending", state_module.state["taiyi_phase"])
            self.assertEqual("空间节点·雷暴", state_module.state["taiyi_pending_node_name"])
            self.assertEqual(0, state_module.state["taiyi_node_search_msg_id"])
            fire_mock.assert_called_once()
            audit_mock.assert_awaited_once()

    async def test_taiyi_real_node_define_success_closes_cycle(self):
        send_as_id = 8659059191
        now = 1_780_710_027.0
        self._prepare_identity(send_as_id, "WalterWA2000")
        reply_to = SimpleNamespace(id=9918486, sender_id=send_as_id, raw_text=".定星 空间节点·雷暴")

        with state_module.use_identity(send_as_id):
            state_module.state["taiyi_enabled"] = True
            state_module.state["taiyi_node_search_enabled"] = True
            state_module.state["taiyi_phase"] = "define_pending"
            state_module.state["taiyi_pending_node_name"] = "空间节点·雷暴"
            state_module.state["taiyi_node_define_msg_id"] = 9918486
            state_module.state["next_taiyi_cycle_time"] = 0

            with (
                patch.object(taiyi, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(taiyi, "save_state"),
            ):
                handled = await taiyi.handle_taiyi_node_define_reply(
                    real_text("taiyi.node_define.success"),
                    now,
                    reply_to,
                    matched_family="taiyi_node_define",
                )

            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["taiyi_phase"])
            self.assertEqual("", state_module.state["taiyi_pending_node_name"])
            self.assertEqual(0, state_module.state["taiyi_node_define_msg_id"])
            self.assertGreater(state_module.state["next_taiyi_cycle_time"], now)
            audit_text = audit_mock.await_args.args[0]
            self.assertIn("空间节点·雷暴", audit_text)
            self.assertIn("逆灵通道坐标", audit_text)

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
