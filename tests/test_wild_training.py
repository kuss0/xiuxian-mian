import asyncio
import copy
import unittest
from unittest.mock import AsyncMock, patch

from model import state as state_module
from model.features import wild_training


class WildTrainingMiniAppTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module.ensure_identity_registered(991201)
        state_module.update_send_as_profile(991201, username="wild")
        wild_training._WILD_TRAINING_MINIAPP_TASKS.clear()
        wild_training._WILD_TRAINING_MINIAPP_RUN_LOCK = None
        wild_training._WILD_TRAINING_MINIAPP_LAST_RUN_AT = 0

    async def asyncTearDown(self):
        tasks = list(wild_training._WILD_TRAINING_MINIAPP_TASKS.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        wild_training._WILD_TRAINING_MINIAPP_TASKS.clear()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _enable(self, *, now=1_700_000_000.0, strategy="谨慎", tianxing=False):
        with state_module.use_identity(991201) as identity_state:
            identity_state["wild_training_enabled"] = True
            identity_state["wild_training_strategy"] = strategy
            identity_state["next_wild_training_time"] = now
            identity_state["wild_training_reply_to_msg_id"] = 0
            identity_state["wild_training_reply_due_at"] = 0
            identity_state["wild_training_retry_count"] = 0
            identity_state["wild_training_last_error"] = ""
            identity_state["tianxing_enabled"] = tianxing

    def test_clear_state_removes_command_era_and_tianxing_pending(self):
        self._enable()
        with state_module.use_identity(991201) as identity_state:
            identity_state["wild_training_reply_to_msg_id"] = 123
            identity_state["wild_training_reply_due_at"] = 1_700_000_900.0
            identity_state["wild_training_tianxing_prepare_retry_at"] = 1_700_000_800.0

        with state_module.use_identity(991201), patch.object(wild_training, "mark_dirty"):
            wild_training.clear_wild_training_state(persist=False)

        self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
        self.assertEqual(0, state_module.state["wild_training_reply_due_at"])
        self.assertEqual(0, state_module.state["wild_training_tianxing_prepare_retry_at"])
        self.assertEqual(0, state_module.state["next_wild_training_time"])

    async def test_strategy_setting_keeps_original_three_choices(self):
        self._enable(strategy="均衡")
        with state_module.use_identity(991201), patch.object(wild_training, "save_state"):
            ok, message = await wild_training.apply_wild_training_strategy("深入")
        self.assertTrue(ok)
        self.assertEqual("深入", state_module.state["wild_training_strategy"])
        self.assertIn("深入", message)
        self.assertEqual("谨慎", wild_training.normalize_wild_training_strategy("未知"))

    async def test_disabled_module_never_queues_miniapp(self):
        with state_module.use_identity(991201), \
                patch.object(wild_training, "_launch_wild_training_miniapp_worker") as launch_mock:
            await wild_training.run_wild_training_scheduler(1_700_000_000.0)
        launch_mock.assert_not_called()

    async def test_future_server_timer_does_not_execute_action(self):
        now = 1_700_000_000.0
        self._enable(now=now + 43_200)
        with state_module.use_identity(991201), \
                patch.object(wild_training, "build_tianxing_consume_window", return_value=[]), \
                patch.object(wild_training, "_launch_wild_training_miniapp_worker") as launch_mock:
            await wild_training.run_wild_training_scheduler(now)
        launch_mock.assert_not_called()
        self.assertEqual(now + 43_200, state_module.state["next_wild_training_time"])

    async def test_due_non_tianxing_queues_public_entry_without_game_command(self):
        now = 1_700_000_000.0
        self._enable(now=now, strategy="均衡")
        result = {
            "ok": True,
            "message": "妖兽遭遇｜修为+1200",
            "extra": {
                "acted": True,
                "completed": True,
                "transport_ok": True,
                "strategy": "均衡",
                "mode": "balanced",
                "next_time": now + 43_200,
                "action_result": {"ok": True, "completed": True, "title": "妖兽遭遇", "message": "修为+1200"},
            },
        }
        with state_module.use_identity(991201), \
                patch.object(wild_training, "_wild_training_public_entry_urls", return_value=["https://t.me/fanrenxiuxian_bot?startapp=df_TEST"]), \
                patch.object(wild_training, "run_cave_public_wild_training", new=AsyncMock(return_value=result)) as run_mock, \
                patch.object(wild_training.time, "time", return_value=now), \
                patch.object(wild_training, "send_game_command", new=AsyncMock()) as send_mock, \
                patch.object(wild_training, "send_audit_log", new=AsyncMock()), \
                patch.object(wild_training, "save_state"):
            await wild_training.run_wild_training_scheduler(now)
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        run_mock.assert_awaited_once()
        self.assertEqual("均衡", run_mock.await_args.args[2])
        send_mock.assert_not_awaited()
        self.assertEqual(now + 43_200, state_module.state["next_wild_training_time"])
        self.assertIn("均衡", state_module.state["wild_training_last_result"])

    async def test_server_cooldown_sync_uses_returned_next_time(self):
        now = 1_700_000_000.0
        self._enable(now=now)
        result = {
            "ok": True,
            "message": "MiniApp 野外历练尚未到期",
            "extra": {"acted": False, "next_time": now + 43_200, "phase": "cooldown"},
        }
        with state_module.use_identity(991201), patch.object(wild_training, "save_state"):
            outcome = await wild_training._apply_miniapp_result(result, now)
        self.assertEqual("cooldown", outcome)
        self.assertEqual(now + 43_200, state_module.state["next_wild_training_time"])
        self.assertEqual(0, state_module.state["wild_training_retry_count"])

    async def test_transport_failure_backs_off_without_replaying_action(self):
        now = 1_700_000_000.0
        self._enable(now=now)
        result = {
            "ok": False,
            "message": "ReadTimeout",
            "extra": {"acted": True, "transport_ok": False, "phase": "action_unknown"},
        }
        with state_module.use_identity(991201), \
                patch.object(wild_training, "save_state"), \
                patch.object(wild_training.random, "uniform", return_value=wild_training.WILD_TRAINING_RETRY_MIN_SEC):
            outcome = await wild_training._apply_miniapp_result(result, now)
        self.assertEqual("failed", outcome)
        self.assertEqual(1, state_module.state["wild_training_retry_count"])
        self.assertEqual(now + wild_training.WILD_TRAINING_MINIAPP_FAILURE_BACKOFF_SEC, state_module.state["next_wild_training_time"])

    async def test_tianxing_route_must_release_before_miniapp_queue(self):
        now = 1_700_000_000.0
        self._enable(now=now, strategy="深入", tianxing=True)
        with state_module.use_identity(991201), \
                patch.object(wild_training, "_prepare_wild_training_tianxing_route", new=AsyncMock(return_value=False)) as prep_mock, \
                patch.object(wild_training, "_launch_wild_training_miniapp_worker") as launch_mock:
            await wild_training.run_wild_training_scheduler(now)
        prep_mock.assert_awaited_once()
        launch_mock.assert_not_called()

    def test_tianxing_preserves_configured_mode_and_only_downgrades_unprotected_deep(self):
        now = 1_700_000_000.0
        for strategy in ("谨慎", "均衡"):
            self._enable(now=now, strategy=strategy, tianxing=True)
            with state_module.use_identity(991201):
                self.assertEqual(strategy, wild_training._effective_wild_training_strategy(now))
        self._enable(now=now, strategy="深入", tianxing=True)
        with state_module.use_identity(991201):
            self.assertEqual("谨慎", wild_training._effective_wild_training_strategy(now))
            state_module.state["tianxing_observation"] = {
                "current_change": "探索",
                "current_change_until": now + 3600,
            }
            self.assertEqual("深入", wild_training._effective_wild_training_strategy(now))

    def test_no_cooldown_followup_cannot_reuse_consumed_tianxing_prediction(self):
        now = 1_700_000_000.0
        self._enable(now=now, strategy="谨慎", tianxing=True)
        with state_module.use_identity(991201) as identity_state:
            identity_state["tianxing_observation"] = {
                "current_prediction": "探索",
                "current_prediction_until": now + 8 * 3600,
                "current_prediction_set_at": now - 30,
                "prediction_consumed_route": "探索",
                "prediction_consumed_at": now - 10,
                "current_change": "探索",
                "current_change_until": now + 6 * 3600,
            }
            self.assertFalse(wild_training._has_active_tianxing_explore_prediction(now))
            self.assertTrue(wild_training._has_active_tianxing_explore_change(now))

    async def test_tianxing_result_is_consumed_from_miniapp_raw_message(self):
        now = 1_700_000_000.0
        self._enable(now=now, tianxing=True)
        result = {
            "ok": True,
            "message": "改命脱险",
            "extra": {
                "acted": True,
                "completed": True,
                "strategy": "深入",
                "next_time": now + 43_200,
                "action_result": {
                    "ok": True,
                    "completed": True,
                    "title": "改命脱险",
                    "rawMessage": "【野外历练 · 改命脱险】\n【推命命中】探索\n【改命回天】",
                },
            },
        }
        with state_module.use_identity(991201), \
                patch.object(wild_training, "apply_tianxing_passive", return_value=True) as passive_mock, \
                patch.object(wild_training, "mark_tianxing_route_result_unknown") as unknown_mock, \
                patch.object(wild_training, "send_audit_log", new=AsyncMock()), \
                patch.object(wild_training, "save_state"):
            outcome = await wild_training._apply_miniapp_result(result, now)
        self.assertEqual("completed", outcome)
        passive_mock.assert_called_once()
        unknown_mock.assert_not_called()
        self.assertEqual(now + 43_200, state_module.state["next_wild_training_time"])

    async def test_phaseful_cleanup_only_clears_legacy_pending(self):
        self._enable()
        with state_module.use_identity(991201) as identity_state:
            identity_state["wild_training_reply_to_msg_id"] = 987
            identity_state["wild_training_reply_due_at"] = 1_700_000_500.0
        with state_module.use_identity(991201), \
                patch.object(wild_training, "save_state"), \
                patch.object(wild_training, "send_game_command", new=AsyncMock()) as send_mock:
            changed = await wild_training.run_wild_training_phaseful_cleanup_scheduler(1_700_000_600.0)
        self.assertTrue(changed)
        self.assertEqual(0, state_module.state["wild_training_reply_to_msg_id"])
        send_mock.assert_not_awaited()

    async def test_malformed_timer_is_reinitialized_without_action(self):
        self._enable()
        with state_module.use_identity(991201) as identity_state:
            identity_state["next_wild_training_time"] = "冷却中"
        with state_module.use_identity(991201), \
                patch.object(wild_training.random, "uniform", return_value=600), \
                patch.object(wild_training, "save_state"), \
                patch.object(wild_training, "_launch_wild_training_miniapp_worker") as launch_mock:
            await wild_training.run_wild_training_scheduler(1_700_000_000.0)
        launch_mock.assert_not_called()
        self.assertEqual(1_700_000_600.0, state_module.state["next_wild_training_time"])


if __name__ == "__main__":
    unittest.main()
