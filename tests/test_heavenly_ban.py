import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import config as config_module
from model import state as state_module
from model.features import concubine, heavenly_ban, passive_inbox


class HeavenlyBanTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._offline_snapshot = copy.deepcopy(config_module._offline_accounts)
        self._passive_stats_snapshot = copy.deepcopy(passive_inbox._passive_stats)
        self._passive_observed_snapshot = dict(passive_inbox._observed_passive_events)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module._meta_state["identity_account_map"] = {}
        passive_inbox._passive_stats = copy.deepcopy(passive_inbox._PASSIVE_STATS_DEFAULT)
        passive_inbox._observed_passive_events = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        config_module._offline_accounts.clear()
        config_module._offline_accounts.update(copy.deepcopy(self._offline_snapshot))
        passive_inbox._passive_stats = self._passive_stats_snapshot
        passive_inbox._observed_passive_events = self._passive_observed_snapshot

    def _prepare_identity(self, identity_id=8659059191, account_id=7001, username="WalterWA2000"):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(
            identity_id,
            username=username,
            label=username,
            enabled=True,
        )
        state_module.set_identity_account(identity_id, account_id)
        with state_module.use_identity(identity_id):
            state_module.state["pending_tasks"] = {123: {"cmd": ".钓鱼状态"}}
        return identity_id

    async def test_heavenly_ban_disables_identity_account_and_alerts_five_times(self):
        identity_id = self._prepare_identity()
        text = "【天道封禁】修士 @WalterWA2000 已被打上封禁烙印。"

        with (
            patch.object(heavenly_ban, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(heavenly_ban, "save_state"),
        ):
            result = await heavenly_ban.handle_heavenly_ban_text(text, now=1_700_000_000.0)

        self.assertTrue(result["handled"])
        self.assertTrue(result["matched"])
        self.assertEqual(identity_id, result["identity_id"])
        self.assertFalse(state_module.get_identity_enabled(identity_id))
        self.assertTrue(config_module.is_account_offline(7001))
        with state_module.use_identity(identity_id):
            self.assertEqual({}, state_module.state["pending_tasks"])
        self.assertEqual(5, audit_mock.await_count)
        self.assertTrue(all(call.kwargs.get("priority") == "high" for call in audit_mock.await_args_list))
        self.assertIn("提醒 5/5", audit_mock.await_args_list[-1].args[0])

    async def test_heavenly_pardon_restores_identity_and_account(self):
        identity_id = self._prepare_identity()
        state_module.set_identity_enabled(identity_id, False)
        config_module.mark_account_offline(7001, "检测到天道封禁/封禁烙印：identity=8659059191")
        with state_module.use_identity(identity_id):
            state_module.state["heavenly_ban_active"] = True
            state_module.state["heavenly_ban_prev_identity_enabled"] = True
            state_module.state["heavenly_ban_reason"] = "【天道封禁】修士 @WalterWA2000 已被打上封禁烙印。"

        with (
            patch.object(heavenly_ban, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(heavenly_ban, "save_state"),
        ):
            result = await heavenly_ban.handle_heavenly_pardon_text(
                "✅ 【天道赦免】\n罪业已洗刷，封禁已解除！",
                now=1_700_000_100.0,
                identity_id_hint=identity_id,
            )

        self.assertTrue(result["handled"])
        self.assertTrue(result["matched"])
        self.assertEqual(identity_id, result["identity_id"])
        self.assertTrue(state_module.get_identity_enabled(identity_id))
        self.assertFalse(config_module.is_account_offline(7001))
        with state_module.use_identity(identity_id):
            self.assertFalse(state_module.state["heavenly_ban_active"])
            self.assertEqual("", state_module.state["heavenly_ban_reason"])
        audit_mock.assert_awaited_once()

    async def test_passive_inbox_routes_heavenly_ban_before_module_parsing(self):
        identity_id = self._prepare_identity(username="tutuerduoxiao")
        text = "【天道封禁】修士 @tutuerduoxiao 已被打上封禁烙印。"

        with (
            patch.object(heavenly_ban, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(heavenly_ban, "save_state"),
            patch.object(passive_inbox, "_save_passive_stats"),
        ):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=1_700_000_000.0,
                reply_context={"send_as_id": identity_id, "family": "concubine_dream"},
                event=SimpleNamespace(id=991, chat_id=1001),
                event_type="test",
            )

        self.assertTrue(handled)
        self.assertFalse(state_module.get_identity_enabled(identity_id))
        self.assertEqual(5, audit_mock.await_count)
        recent = passive_inbox.get_passive_inbox_snapshot()["recent"][-1]
        self.assertEqual("heavenly_ban", recent["module"])
        self.assertEqual(identity_id, recent["identity_id"])

    async def test_passive_inbox_routes_heavenly_pardon_before_module_parsing(self):
        identity_id = self._prepare_identity(username="WalterWA2000")
        state_module.set_identity_enabled(identity_id, False)
        config_module.mark_account_offline(7001, "检测到天道封禁/封禁烙印：identity=8659059191")

        with (
            patch.object(heavenly_ban, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(heavenly_ban, "save_state"),
            patch.object(passive_inbox, "_save_passive_stats"),
        ):
            handled = await passive_inbox.handle_passive_module_card(
                "✅ 【天道赦免】\n罪业已洗刷，封禁已解除！",
                now=1_700_000_100.0,
                reply_context={"send_as_id": identity_id, "family": "heavenly_pardon", "reply_to_msg_id": 11296841},
                event=SimpleNamespace(id=11296843, chat_id=1001),
                event_type="test",
            )

        self.assertTrue(handled)
        self.assertTrue(state_module.get_identity_enabled(identity_id))
        self.assertFalse(config_module.is_account_offline(7001))
        audit_mock.assert_awaited_once()
        recent = passive_inbox.get_passive_inbox_snapshot()["recent"][-1]
        self.assertEqual("heavenly_pardon", recent["module"])
        self.assertEqual(identity_id, recent["identity_id"])

    async def test_concubine_dream_heavenly_ban_does_not_schedule_cooldown(self):
        identity_id = self._prepare_identity(username="iceeet1")
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["concubine_enabled"] = True
            state_module.state["concubine_phase"] = "dream_pending"
            state_module.state["concubine_dream_msg_id"] = 777
            state_module.state["concubine_dream_due_at"] = now + 3600
            state_module.state["next_concubine_time"] = now + 3600

            with (
                patch.object(heavenly_ban, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(heavenly_ban, "save_state"),
                patch.object(concubine, "save_state"),
            ):
                handled = await concubine.handle_concubine_dream_reply(
                    "【天道封禁】修士 @iceeet1 已被打上封禁烙印。",
                    now,
                    SimpleNamespace(id=777, raw_text=".入梦寻图"),
                    matched_family="concubine_dream",
                )

            self.assertTrue(handled)
            self.assertFalse(state_module.get_identity_enabled(identity_id))
            self.assertEqual(0, state_module.state["concubine_dream_due_at"])
            self.assertEqual(0, state_module.state["next_concubine_time"])
            self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(5, audit_mock.await_count)

    async def test_concubine_scheduler_recovers_persisted_heavenly_ban_error(self):
        identity_id = self._prepare_identity(username="WalterWA2000")
        now = 1_700_000_000.0
        with state_module.use_identity(identity_id):
            state_module.state["concubine_enabled"] = True
            state_module.state["concubine_phase"] = "idle"
            state_module.state["concubine_last_error"] = (
                "未识别的入梦寻图回复: 【天道封禁】 检测到违规行为，"
                "修士 @WalterWA2000 已被打上封禁烙印！"
            )
            state_module.state["concubine_dream_due_at"] = now + 3600
            state_module.state["next_concubine_time"] = now + 3600

            with (
                patch.object(heavenly_ban, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(heavenly_ban, "save_state"),
                patch.object(concubine, "save_state"),
            ):
                await concubine._run_concubine_scheduler(now)

            self.assertFalse(state_module.get_identity_enabled(identity_id))
            self.assertEqual("", state_module.state["concubine_last_error"])
            self.assertEqual(0, state_module.state["concubine_dream_due_at"])
            self.assertEqual(0, state_module.state["next_concubine_time"])
            self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(5, audit_mock.await_count)

    async def test_concubine_startup_restore_clears_persisted_heavenly_ban_without_waiting_due(self):
        identity_id = self._prepare_identity(username="WalterWA2000")
        now = 1_700_000_000.0
        scheduled = []

        def fake_fire_and_forget(coro):
            scheduled.append(coro)
            coro.close()

        with state_module.use_identity(identity_id):
            state_module.state["concubine_enabled"] = True
            state_module.state["concubine_phase"] = "idle"
            state_module.state["concubine_last_error"] = (
                "未识别的入梦寻图回复: 【天道封禁】 检测到违规行为，"
                "修士 @WalterWA2000 已被打上封禁烙印！"
            )
            state_module.state["concubine_dream_due_at"] = now + 3600
            state_module.state["next_concubine_time"] = now + 3600

            with patch.object(concubine, "_fire_and_forget", side_effect=fake_fire_and_forget):
                next_time = concubine.restore_concubine_runtime(now)

            self.assertEqual(0, next_time)
            self.assertEqual("", state_module.state["concubine_last_error"])
            self.assertEqual(0, state_module.state["concubine_dream_due_at"])
            self.assertEqual(0, state_module.state["next_concubine_time"])
            self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(1, len(scheduled))


if __name__ == "__main__":
    unittest.main()
