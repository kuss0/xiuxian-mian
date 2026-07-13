import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import app
from model import state as state_module


class _SendAsProbeClient:
    async def get_input_entity(self, entity_id):
        return SimpleNamespace(id=int(entity_id))

    async def __call__(self, request):
        return SimpleNamespace(peers=[
            SimpleNamespace(peer=SimpleNamespace(channel_id=3504367852)),
        ])


class ChannelSendAsHealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module._meta_state["identity_account_map"] = {}
        state_module._meta_state["channel_send_as_health"] = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    async def test_probe_restores_frozen_channel_identities_after_permission_returns(self):
        now = 1_700_000_000.0
        account_id = 301299112
        game_group_id = -1002083016447
        first_id = 3504367852
        second_id = 3581351795
        for identity_id in (account_id, first_id, second_id):
            state_module.ensure_identity_registered(identity_id)
            state_module.set_identity_account(identity_id, account_id)
        state_module.set_identity_enabled(first_id, False)
        state_module.set_identity_enabled(second_id, False)
        state_module.set_game_group_id(game_group_id)
        state_module.set_channel_send_as_health({
            "status": "closed",
            "account_id": account_id,
            "game_group_id": game_group_id,
            "next_probe_at": now - 1,
            "restore_identity_ids": [first_id, second_id],
            "frozen_identity_ids": [first_id, second_id],
        })

        with (
            patch.object(app, "get_registered_client", return_value=_SendAsProbeClient()),
            patch.object(app, "is_account_offline", return_value=False),
            patch.object(app, "initialize_identity_runtime") as initialize_mock,
            patch.object(app, "spread_overdue_runtime_timers", return_value=2) as spread_mock,
            patch.object(app, "extend_global_recovery_throttle_for_spread") as throttle_mock,
            patch.object(app, "save_state") as save_mock,
            patch.object(app, "send_audit_log", new=AsyncMock()) as audit_mock,
        ):
            await app.run_channel_send_as_health_scheduler(now)

        self.assertTrue(state_module.get_identity_enabled(first_id))
        self.assertTrue(state_module.get_identity_enabled(second_id))
        self.assertEqual("open", state_module.get_channel_send_as_health()["status"])
        self.assertEqual(2, initialize_mock.call_count)
        spread_mock.assert_called_once_with(now, reason="频道身份恢复")
        throttle_mock.assert_called_once_with(now, reason="频道身份恢复")
        save_mock.assert_called_once()
        audit_mock.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
