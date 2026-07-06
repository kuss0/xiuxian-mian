import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import passive_inbox, stargazer, storage_bag


class StargazerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    def test_queue_followup_clears_stale_panel_timer(self):
        now = 1000.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        with state_module.use_identity(identity_id):
            state_module.state["next_stargazer_panel_time"] = now + 1
            stargazer._queue_stargazer_followup_action(now, "guide", 8)

            self.assertEqual("queue_guide", state_module.state["stargazer_last_action"])
            self.assertEqual("guide", state_module.state["stargazer_queued_action"])
            self.assertEqual(now + 8, state_module.state["stargazer_followup_due_at"])
            self.assertEqual(0, state_module.state["next_stargazer_panel_time"])

    def test_panel_parser_accepts_collectible_slot_wording(self):
        text = (
            "【星宫 · 观星台】 (引星盘总数: 2座)\n\n"
            "1号引星盘: 庚金星 - 可收集 💎\n"
            "2号引星盘: 庚金星 - 精华已成 💎"
        )

        parsed = stargazer._parse_stargazer_panel(text)

        self.assertIsNotNone(parsed)
        self.assertEqual(2, parsed["total_slots"])
        self.assertEqual(2, parsed["ready_slot_count"])
        self.assertTrue(parsed["all_ready"])

    async def test_scheduler_uses_durable_queued_action_when_last_action_changes(self):
        now = 1000.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            stargazer._queue_stargazer_followup_action(now - 10, "soothe", 1)
            state_module.state["stargazer_last_action"] = "soothe"

            with patch.object(stargazer, "_send_stargazer_soothe", new=AsyncMock(return_value=True)) as send_soothe:
                await stargazer.run_stargazer_scheduler(now)

            send_soothe.assert_awaited_once_with(now)
            self.assertEqual("", state_module.state["stargazer_queued_action"])
            self.assertEqual(0, state_module.state["stargazer_followup_due_at"])

    async def test_scheduler_blocks_dirty_panel_cooldown_without_rewriting(self):
        now = 1000.0
        identity_id = 3756719391
        dirty_cooldown = "观星冷却数据异常"
        state_module.ensure_identity_registered(identity_id)

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["next_stargazer_panel_time"] = dirty_cooldown

            with (
                patch.object(stargazer, "_queue_stargazer_action", new=AsyncMock()) as queue_mock,
                patch.object(stargazer, "save_state") as save_mock,
            ):
                await stargazer.run_stargazer_scheduler(now)

            queue_mock.assert_not_awaited()
            save_mock.assert_not_called()
            self.assertEqual(dirty_cooldown, state_module.state["next_stargazer_panel_time"])

    def test_status_text_tolerates_dirty_panel_cooldown(self):
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        with state_module.use_identity(identity_id):
            state_module.state["next_stargazer_panel_time"] = "观星冷却数据异常"

            text = stargazer.get_stargazer_status_text()

        self.assertIn("🔭 观星台", text)
        self.assertIn("未设置", text)

    async def test_miniapp_entry_runs_miniapp_flow_and_keeps_legacy_text_chain_paused(self):
        now = 1000.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)
        button = SimpleNamespace(
            text="进入灵圃",
            button=SimpleNamespace(url="https://t.me/fanrenxiuxian_bot/app?startapp=farm_SECRET999"),
        )
        event = SimpleNamespace(id=456, message=SimpleNamespace(buttons=[[button]]))

        class Reply:
            raw_text = ".观星台"

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_followup_due_at"] = now - 1
            state_module.state["stargazer_queued_action"] = "guide"
            state_module.state["stargazer_last_action"] = "queue_guide"
            state_module.state["next_stargazer_panel_time"] = now - 1
            state_module.state["stargazer_collect_due_at"] = now - 1
            state_module.state["stargazer_collect_ready"] = True

            with (
                patch.object(stargazer, "send_audit_log", new=AsyncMock(return_value=True)) as audit_mock,
                patch.object(stargazer, "_queue_stargazer_action", new=AsyncMock()) as queue_mock,
                patch.object(stargazer, "run_stargazer_miniapp_production_flow", new=AsyncMock(return_value={
                    "ok": True,
                    "status": "wait",
                    "data": {
                        "farm_state": {
                            "total_slots": 2,
                            "declared_total_slots": 2,
                            "idle_slot_count": 0,
                            "dim_slot_count": 0,
                            "ready_slot_count": 0,
                            "max_wait": 60,
                            "all_ready": False,
                            "plots": [],
                        },
                        "action_counts": {"soothe": 1, "collect": 1, "pull": 1},
                        "item_deltas": {"星辰精华": 2},
                    },
                })) as flow_mock,
                patch.object(stargazer, "apply_storage_bag_item_deltas") as storage_mock,
                patch.object(stargazer, "save_state"),
            ):
                handled = await stargazer.handle_stargazer_miniapp_entry(
                    event,
                    "【星宫 · 观星台】\n@lab 的引星盘已接入宗门灵圃。\n\n点击下方 进入灵圃，牵引星辰与收取星辰精华。",
                    now,
                    Reply(),
                    matched_family="stargazer_panel",
                    result_msg_id=456,
                )
                await stargazer.run_stargazer_scheduler(now + 10)

            self.assertTrue(handled)
            self.assertEqual(2, audit_mock.await_count)
            flow_mock.assert_awaited_once()
            storage_mock.assert_called_once_with(identity_id, {"星辰精华": 2})
            queue_mock.assert_not_awaited()
            self.assertEqual("miniapp_waiting_panel", state_module.state["stargazer_last_action"])
            self.assertEqual("", state_module.state["stargazer_queued_action"])
            self.assertEqual(0, state_module.state["stargazer_followup_due_at"])
            self.assertGreater(state_module.state["next_stargazer_panel_time"], now + 60)
            self.assertEqual(456, state_module.state["stargazer_last_panel_msg_id"])
            self.assertFalse(state_module.state["stargazer_collect_ready"])

    async def test_scheduler_queues_panel_when_numeric_panel_cooldown_due(self):
        now = 1000.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["next_stargazer_panel_time"] = now - 1

            with patch.object(stargazer, "_queue_stargazer_action", new=AsyncMock(return_value=True)) as queue_mock:
                await stargazer.run_stargazer_scheduler(now)

            queue_mock.assert_awaited_once_with(now, "panel", audit_text="🌠 观星台到时，查面板")

    async def test_scheduler_keeps_future_numeric_panel_cooldown_blocked(self):
        now = 1000.0
        identity_id = 3756719391
        next_panel_time = now + 60
        state_module.ensure_identity_registered(identity_id)

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["next_stargazer_panel_time"] = next_panel_time

            with patch.object(stargazer, "_queue_stargazer_action", new=AsyncMock()) as queue_mock:
                await stargazer.run_stargazer_scheduler(now)

            queue_mock.assert_not_awaited()
            self.assertEqual(next_panel_time, state_module.state["next_stargazer_panel_time"])

    async def test_scheduler_blocks_dirty_followup_due_without_clearing_queue(self):
        now = 1000.0
        identity_id = 3756719391
        dirty_due = "观星后续时间异常"
        state_module.ensure_identity_registered(identity_id)

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_followup_due_at"] = dirty_due
            state_module.state["stargazer_queued_action"] = "guide"
            state_module.state["stargazer_last_action"] = "queue_guide"

            with (
                patch.object(stargazer, "_send_stargazer_guide", new=AsyncMock()) as guide_mock,
                patch.object(stargazer, "_queue_stargazer_action", new=AsyncMock()) as queue_mock,
                patch.object(stargazer, "save_state") as save_mock,
            ):
                await stargazer.run_stargazer_scheduler(now)

            guide_mock.assert_not_awaited()
            queue_mock.assert_not_awaited()
            save_mock.assert_not_called()
            self.assertEqual(dirty_due, state_module.state["stargazer_followup_due_at"])
            self.assertEqual("guide", state_module.state["stargazer_queued_action"])
            self.assertEqual("queue_guide", state_module.state["stargazer_last_action"])

    async def test_guide_send_disables_blind_retry_and_schedules_panel_fallback(self):
        now = 1500.0
        sent_at = now + 3
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        async def fake_send(command, **kwargs):
            state_module.state["pending_tasks"][123] = {
                "cmd": command,
                "sent_at": sent_at,
                "retry": 0,
                "timeout": 42,
                "max_retry": kwargs.get("max_retry"),
            }
            return SimpleNamespace(id=123, sent_at=sent_at)

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.set_stargazer_star_choice(identity_id, "天雷星")

            with (
                patch.object(stargazer, "send_game_command", side_effect=fake_send) as send_mock,
                patch.object(stargazer.random, "uniform", return_value=7),
                patch.object(stargazer, "save_state"),
            ):
                sent = await stargazer._send_stargazer_guide(now)

            self.assertTrue(sent)
            send_mock.assert_awaited_once_with(".牵引星辰 天雷星", max_retry=0)
            self.assertEqual(sent_at + 42 + 7, state_module.state["next_stargazer_panel_time"])
            self.assertEqual(0, state_module.state["pending_tasks"][123]["max_retry"])

    async def test_passive_collect_reply_does_not_overwrite_active_queue(self):
        now = 1000.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_followup_due_at"] = now + 8
            state_module.state["stargazer_last_action"] = "queue_guide"
            state_module.state["stargazer_queued_action"] = "guide"
            state_module.state["stargazer_ready_slot_count"] = 8

        text = "收集完成，成功从 8 座引星盘上收集星辰精华。"
        with patch.object(passive_inbox, "_save_passive_stats"):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={"send_as_id": identity_id, "family": "stargazer_collect"},
            )

        self.assertFalse(handled)
        with state_module.use_identity(identity_id):
            self.assertEqual("queue_guide", state_module.state["stargazer_last_action"])
            self.assertEqual("guide", state_module.state["stargazer_queued_action"])
            self.assertEqual(now + 8, state_module.state["stargazer_followup_due_at"])
            self.assertEqual(8, state_module.state["stargazer_ready_slot_count"])

    async def test_command_echoes_do_not_trigger_soothe_or_collect_fallbacks(self):
        now = 1000.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        class Reply:
            raw_text = ".安抚星辰"

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_last_action"] = "soothe"
            state_module.state["stargazer_followup_due_at"] = 0
            soothe_handled = await stargazer.handle_stargazer_soothe_reply(
                ".安抚星辰",
                now,
                Reply(),
                matched_family="stargazer_soothe",
            )
            collect_reply = Reply()
            collect_reply.raw_text = ".收集精华"
            collect_handled = await stargazer.handle_stargazer_collect_reply(
                ".收集精华",
                now,
                collect_reply,
                matched_family="stargazer_collect",
            )

            self.assertFalse(soothe_handled)
            self.assertFalse(collect_handled)
            self.assertEqual("soothe", state_module.state["stargazer_last_action"])
            self.assertEqual(0, state_module.state["stargazer_followup_due_at"])

    async def test_real_soothe_success_reply_rechecks_panel_before_collect(self):
        now = 1800.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        class Reply:
            raw_text = ".安抚星辰"

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_last_action"] = "soothe"

            with patch.object(stargazer, "_queue_stargazer_action", new=AsyncMock(return_value=True)) as queue_mock:
                handled = await stargazer.handle_stargazer_soothe_reply(
                    "你消耗了 320 点修为，成功安抚了 8 座引星盘的狂暴星力！\n因有侍妾【妍丽】相助，本次消耗大幅减少。",
                    now,
                    Reply(),
                    matched_family="stargazer_soothe",
                )

            self.assertTrue(handled)
            queue_mock.assert_awaited_once()
            self.assertEqual("panel", queue_mock.await_args.args[1])
            self.assertIn("回查观星台", queue_mock.await_args.kwargs["audit_text"])

    async def test_soothe_success_rechecks_panel_before_collect(self):
        now = 1800.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        class Reply:
            raw_text = ".安抚星辰"

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_last_action"] = "soothe"
            state_module.state["stargazer_dim_slot_count"] = 2

            with patch.object(stargazer, "_queue_stargazer_action", new=AsyncMock(return_value=True)) as queue_mock:
                handled = await stargazer.handle_stargazer_soothe_reply(
                    "你消耗了 72 点修为，成功安抚了 2 座引星盘的狂暴星力！\n因有侍妾【妍丽】相助，本次消耗大幅减少。",
                    now,
                    Reply(),
                    matched_family="stargazer_soothe",
                )

            self.assertTrue(handled)
            queue_mock.assert_awaited_once()
            self.assertEqual("panel", queue_mock.await_args.args[1])
            self.assertIn("回查观星台", queue_mock.await_args.kwargs["audit_text"])

    async def test_soothe_before_collect_success_rechecks_panel(self):
        now = 1800.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        class Reply:
            raw_text = ".安抚星辰"

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_last_action"] = "soothe"
            state_module.state["stargazer_soothe_before_collect"] = True
            state_module.state["stargazer_queued_action"] = "collect"

            with patch.object(stargazer, "_queue_stargazer_action", new=AsyncMock(return_value=True)) as queue_mock:
                handled = await stargazer.handle_stargazer_soothe_reply(
                    "你消耗了 72 点修为，成功安抚了 2 座引星盘的狂暴星力！\n因有侍妾【妍丽】相助，本次消耗大幅减少。",
                    now,
                    Reply(),
                    matched_family="stargazer_soothe",
                )

            self.assertTrue(handled)
            queue_mock.assert_awaited_once()
            self.assertEqual("panel", queue_mock.await_args.args[1])
            self.assertIn("回查观星台", queue_mock.await_args.kwargs["audit_text"])

    async def test_collect_send_rechecks_panel_when_dim_slots_remain(self):
        now = 2000.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_dim_slot_count"] = 2
            state_module.state["stargazer_queued_action"] = "collect"

            with (
                patch.object(stargazer, "_send_stargazer_panel", new=AsyncMock(return_value=True)) as panel_mock,
                patch.object(stargazer, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                sent = await stargazer._send_stargazer_collect(now)

            self.assertTrue(sent)
            panel_mock.assert_awaited_once_with(now, audit_text="🔭 仍有黯淡盘，收集前回查")
            send_mock.assert_not_awaited()

    async def test_scheduler_collect_fallback_rechecks_panel_when_dim_slots_remain(self):
        now = 2000.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_dim_slot_count"] = 2
            state_module.state["stargazer_followup_due_at"] = now - 1
            state_module.state["stargazer_last_action"] = "queue_collect"
            state_module.state["stargazer_queued_action"] = ""

            with (
                patch.object(stargazer, "_send_stargazer_panel", new=AsyncMock(return_value=True)) as panel_mock,
                patch.object(stargazer, "send_game_command", new=AsyncMock()) as send_mock,
            ):
                await stargazer.run_stargazer_scheduler(now)

            panel_mock.assert_awaited_once_with(now, audit_text="🔭 仍有黯淡盘，收集前回查")
            send_mock.assert_not_awaited()

    async def test_passive_real_soothe_success_updates_state(self):
        now = 1900.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_last_action"] = "soothe"

        with (
            patch.object(passive_inbox, "_save_passive_stats"),
            patch.object(passive_inbox, "save_state"),
        ):
            handled = await passive_inbox.handle_passive_module_card(
                "你消耗了 80 点修为，成功安抚了 8 座引星盘的狂暴星力！\n因有侍妾【月婵】相助，本次消耗大幅减少。",
                now=now,
                reply_context={"send_as_id": identity_id, "family": "stargazer_soothe"},
            )

        self.assertTrue(handled)
        with state_module.use_identity(identity_id):
            self.assertEqual("passive_soothe_done", state_module.state["stargazer_last_action"])
            self.assertEqual("panel", state_module.state["stargazer_queued_action"])
            self.assertEqual(now + 5, state_module.state["stargazer_followup_due_at"])

    async def test_passive_soothe_success_clears_old_collect_queue(self):
        now = 1900.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_last_action"] = "queue_collect"
            state_module.state["stargazer_queued_action"] = "collect"
            state_module.state["stargazer_followup_due_at"] = now + 60
            state_module.state["stargazer_soothe_before_collect"] = True

        with (
            patch.object(passive_inbox, "_save_passive_stats"),
            patch.object(passive_inbox, "save_state"),
        ):
            handled = await passive_inbox.handle_passive_module_card(
                "你消耗了 72 点修为，成功安抚了 2 座引星盘的狂暴星力！\n因有侍妾【妍丽】相助，本次消耗大幅减少。",
                now=now,
                reply_context={"send_as_id": identity_id, "family": "stargazer_soothe"},
            )

        self.assertTrue(handled)
        with state_module.use_identity(identity_id):
            self.assertEqual("passive_soothe_done", state_module.state["stargazer_last_action"])
            self.assertEqual("panel", state_module.state["stargazer_queued_action"])
            self.assertEqual(now + 5, state_module.state["stargazer_followup_due_at"])
            self.assertFalse(state_module.state["stargazer_soothe_before_collect"])

    async def test_passive_panel_with_dim_slots_interrupts_collect_queue(self):
        now = 1950.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)
        panel = (
            "【星宫 · 观星台】 (引星盘总数: 2座)\n\n"
            "1号引星盘: 庚金星 - 精华已成 💎\n"
            "2号引星盘: 庚金星 - 星光黯淡"
        )

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_last_action"] = "queue_collect"
            state_module.state["stargazer_queued_action"] = "collect"
            state_module.state["stargazer_followup_due_at"] = now + 60
            state_module.state["stargazer_dim_slot_count"] = 0

        with (
            patch.object(passive_inbox, "_save_passive_stats"),
            patch.object(passive_inbox, "save_state"),
        ):
            handled = await passive_inbox.handle_passive_module_card(
                panel,
                now=now,
                reply_context={"send_as_id": identity_id, "family": "stargazer_panel"},
            )

        self.assertTrue(handled)
        with state_module.use_identity(identity_id):
            self.assertEqual(1, state_module.state["stargazer_dim_slot_count"])
            self.assertEqual("passive_dim_slot", state_module.state["stargazer_last_action"])
            self.assertEqual("soothe", state_module.state["stargazer_queued_action"])
            self.assertEqual(now + 5, state_module.state["stargazer_followup_due_at"])

    async def test_passive_panel_all_ready_queues_collect(self):
        now = 1960.0
        identity_id = 3756719391
        state_module.ensure_identity_registered(identity_id)
        panel = (
            "【星宫 · 观星台】 (引星盘总数: 2座)\n\n"
            "1号引星盘: 庚金星 - 精华已成 💎\n"
            "2号引星盘: 庚金星 - 可收集 💎"
        )

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_last_action"] = "queue_panel"
            state_module.state["stargazer_queued_action"] = "panel"
            state_module.state["stargazer_followup_due_at"] = now + 60

        with (
            patch.object(passive_inbox, "_save_passive_stats"),
            patch.object(passive_inbox, "save_state"),
        ):
            handled = await passive_inbox.handle_passive_module_card(
                panel,
                now=now,
                reply_context={"send_as_id": identity_id, "family": "stargazer_panel"},
            )

        self.assertTrue(handled)
        with state_module.use_identity(identity_id):
            self.assertEqual(0, state_module.state["stargazer_dim_slot_count"])
            self.assertEqual(2, state_module.state["stargazer_ready_slot_count"])
            self.assertEqual("passive_all_ready", state_module.state["stargazer_last_action"])
            self.assertEqual("collect", state_module.state["stargazer_queued_action"])
            self.assertEqual(now + 5, state_module.state["stargazer_followup_due_at"])

    async def test_collect_success_adds_items_to_cached_storage_bag(self):
        now = 2000.0
        identity_id = 3756719392
        state_module.ensure_identity_registered(identity_id)

        with state_module.use_identity(identity_id):
            state_module.state["stargazer_enabled"] = True
            state_module.state["stargazer_ready_slot_count"] = 8
            state_module.set_storage_bag_records({
                str(identity_id): {
                    "updated_at": 1900,
                    "items": {"金精矿": 1, "灵石": 20},
                    "sections": {"材料": {"金精矿": 1, "灵石": 20}},
                }
            })

            with (
                patch.object(stargazer, "_queue_stargazer_action", new=AsyncMock(return_value=True)),
                patch.object(storage_bag, "save_state"),
            ):
                handled = await stargazer.handle_stargazer_collect_reply(
                    "收集完成！\n你成功从 8 座引星盘上收集了星辰精华！\n你获得了：【金精矿】x15, 【灵石】x110。",
                    now,
                    reply_to=None,
                    matched_family="stargazer_collect",
                )

            self.assertTrue(handled)
            record = state_module.get_storage_bag_records()[str(identity_id)]
            self.assertEqual(16, record["items"]["金精矿"])
            self.assertEqual(130, record["items"]["灵石"])


if __name__ == "__main__":
    unittest.main()
