import copy
import sys
import unittest
from pathlib import Path
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
