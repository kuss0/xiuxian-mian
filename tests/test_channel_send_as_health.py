import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import app
from model import runtime
from model import state as state_module


class _SendAsProbeClient:
    def __init__(
        self,
        available_ids,
        *,
        saveable_ids=None,
        reject_get_send_as=False,
        personal_id=301299112,
        available_ids_by_peer=None,
        saveable_ids_by_peer=None,
    ):
        self.available_ids = list(available_ids)
        self.saveable_ids = set(self.available_ids if saveable_ids is None else saveable_ids)
        self.reject_get_send_as = reject_get_send_as
        self.personal_id = int(personal_id)
        self.save_default_ids = []
        self.save_default_pairs = []
        self.available_ids_by_peer = available_ids_by_peer or {}
        self.saveable_ids_by_peer = saveable_ids_by_peer or {}

    async def get_input_entity(self, entity_id):
        return SimpleNamespace(id=int(entity_id))

    async def __call__(self, request):
        request_name = request.__class__.__name__
        peer_id = int(getattr(getattr(request, "peer", None), "id", 0) or 0)
        if request_name == "GetSendAsRequest":
            if self.reject_get_send_as:
                from telethon.errors import PeerIdInvalidError
                raise PeerIdInvalidError(request=request)
            available_ids = self.available_ids_by_peer.get(peer_id, self.available_ids)
            return SimpleNamespace(peers=[
                SimpleNamespace(peer=SimpleNamespace(channel_id=identity_id))
                for identity_id in available_ids
            ])
        send_as = getattr(request, "send_as", None)
        if request_name == "SaveDefaultSendAsRequest" and send_as is not None:
            send_as_id = int(getattr(send_as, "id", 0) or 0)
            self.save_default_ids.append(send_as_id)
            self.save_default_pairs.append((peer_id, send_as_id))
            saveable_ids = self.saveable_ids_by_peer.get(peer_id, self.saveable_ids)
            if send_as_id != self.personal_id and send_as_id not in saveable_ids:
                from telethon.errors import SendAsPeerInvalidError
                raise SendAsPeerInvalidError(request=request)
        return SimpleNamespace()


class ChannelSendAsHealthTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._invalid_until_snapshot = copy.deepcopy(runtime._SEND_AS_PEER_INVALID_UNTIL)
        self._cohort_invalid_until_snapshot = copy.deepcopy(runtime._CHANNEL_SEND_AS_INVALID_UNTIL)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module._meta_state["identity_account_map"] = {}
        state_module._meta_state["channel_send_as_health"] = {}
        runtime._SEND_AS_PEER_INVALID_UNTIL.clear()
        runtime._CHANNEL_SEND_AS_INVALID_UNTIL.clear()

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        runtime._SEND_AS_PEER_INVALID_UNTIL.clear()
        runtime._SEND_AS_PEER_INVALID_UNTIL.update(self._invalid_until_snapshot)
        runtime._CHANNEL_SEND_AS_INVALID_UNTIL.clear()
        runtime._CHANNEL_SEND_AS_INVALID_UNTIL.update(self._cohort_invalid_until_snapshot)
        super().tearDown()

    def test_public_cave_entry_allows_channel_freeze_but_not_manual_disable(self):
        frozen_id = 3504367852
        manual_disabled_id = 3581351795
        for identity_id in (frozen_id, manual_disabled_id):
            state_module.ensure_identity_registered(identity_id)
            state_module.set_identity_enabled(identity_id, False)
        state_module.set_channel_send_as_health({
            "status": "closed",
            "frozen_identity_ids": [frozen_id, manual_disabled_id],
            "restore_identity_ids": [frozen_id],
        })

        self.assertTrue(state_module.is_cave_public_identity_available(frozen_id))
        self.assertFalse(state_module.is_cave_public_identity_available(manual_disabled_id))

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
        runtime._SEND_AS_PEER_INVALID_UNTIL[(first_id, game_group_id)] = now + 1800
        runtime._CHANNEL_SEND_AS_INVALID_UNTIL[(account_id, game_group_id)] = now + 1800

        client = _SendAsProbeClient([first_id, second_id])
        with (
            patch.object(app, "get_registered_client", return_value=client),
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
        self.assertEqual(0.0, runtime._send_as_peer_invalid_until(
            first_id,
            account_id=account_id,
            game_group_id=game_group_id,
            now=now,
        ))
        spread_mock.assert_called_once_with(now, reason="频道身份恢复")
        throttle_mock.assert_called_once_with(
            now,
            reason="频道身份恢复",
            activate_if_missing=True,
        )
        save_mock.assert_called_once()
        audit_mock.assert_awaited_once()
        self.assertEqual([first_id, second_id, account_id], client.save_default_ids)

    async def test_backup_route_restores_only_after_backup_send_as_probe_succeeds(self):
        now = 1_700_000_000.0
        account_id = 301299112
        primary_group_id = -1002083016447
        backup_group_id = -1001680975844
        frozen_id = 3504367852
        manual_disabled_id = 3581351795
        for identity_id in (account_id, frozen_id, manual_disabled_id):
            state_module.ensure_identity_registered(identity_id)
            state_module.set_identity_account(identity_id, account_id)
            state_module.set_identity_enabled(identity_id, False)
        state_module.set_game_group_id(primary_group_id)
        state_module.set_game_group_route_config({
            "enabled": True,
            "primary_group_id": primary_group_id,
            "backup_group_ids": [backup_group_id],
            "topic_id_by_group": {str(primary_group_id): 0, str(backup_group_id): 7310786},
        })
        state_module.set_channel_send_as_health({
            "status": "closed",
            "account_id": account_id,
            "game_group_id": primary_group_id,
            "next_probe_at": now - 1,
            "restore_identity_ids": [frozen_id],
            "frozen_identity_ids": [frozen_id, manual_disabled_id],
        })

        client = _SendAsProbeClient(
            [frozen_id],
            available_ids_by_peer={primary_group_id: [frozen_id], backup_group_id: [frozen_id]},
            saveable_ids_by_peer={primary_group_id: [], backup_group_id: [frozen_id]},
        )
        with (
            patch.object(app, "get_registered_client", return_value=client) as client_mock,
            patch.object(app, "initialize_identity_runtime") as initialize_mock,
            patch.object(app, "spread_overdue_runtime_timers", return_value=1) as spread_mock,
            patch.object(app, "extend_global_recovery_throttle_for_spread") as throttle_mock,
            patch.object(app, "save_state") as save_mock,
        ):
            await app.run_channel_send_as_health_scheduler(now)

        self.assertTrue(state_module.get_identity_enabled(frozen_id))
        self.assertFalse(state_module.get_identity_enabled(manual_disabled_id))
        self.assertEqual("open", state_module.get_channel_send_as_health()["status"])
        self.assertEqual(0.0, runtime._send_as_peer_invalid_until(
            frozen_id,
            account_id=account_id,
            game_group_id=backup_group_id,
            now=now,
        ))
        self.assertGreater(runtime._send_as_peer_invalid_until(
            frozen_id,
            account_id=account_id,
            game_group_id=primary_group_id,
            now=now,
        ), now)
        client_mock.assert_called_once_with(account_id)
        initialize_mock.assert_called_once_with(frozen_id, now)
        spread_mock.assert_called_once_with(now, reason="频道身份恢复")
        throttle_mock.assert_called_once_with(
            now,
            reason="频道身份恢复",
            activate_if_missing=True,
        )
        save_mock.assert_called_once()

        self.assertEqual(
            [
                (primary_group_id, frozen_id),
                (primary_group_id, account_id),
                (backup_group_id, frozen_id),
                (backup_group_id, account_id),
            ],
            client.save_default_pairs,
        )

    async def test_multi_route_probe_keeps_identity_frozen_when_all_groups_reject(self):
        now = 1_700_000_000.0
        account_id = 301299112
        primary_group_id = -1002083016447
        backup_group_id = -1001680975844
        frozen_id = 3504367852
        for identity_id in (account_id, frozen_id):
            state_module.ensure_identity_registered(identity_id)
            state_module.set_identity_account(identity_id, account_id)
        state_module.set_identity_enabled(frozen_id, False)
        state_module.set_game_group_id(primary_group_id)
        state_module.set_game_group_route_config({
            "enabled": True,
            "primary_group_id": primary_group_id,
            "backup_group_ids": [backup_group_id],
            "topic_id_by_group": {str(primary_group_id): 0, str(backup_group_id): 7310786},
        })
        state_module.set_channel_send_as_health({
            "status": "closed",
            "account_id": account_id,
            "game_group_id": primary_group_id,
            "next_probe_at": now - 1,
            "restore_identity_ids": [frozen_id],
            "frozen_identity_ids": [frozen_id],
        })
        client = _SendAsProbeClient(
            [frozen_id],
            available_ids_by_peer={primary_group_id: [frozen_id], backup_group_id: [frozen_id]},
            saveable_ids_by_peer={primary_group_id: [], backup_group_id: []},
        )

        with (
            patch.object(app, "get_registered_client", return_value=client),
            patch.object(app, "is_account_offline", return_value=False),
            patch.object(app, "initialize_identity_runtime") as initialize_mock,
            patch.object(app, "spread_overdue_runtime_timers") as spread_mock,
            patch.object(app, "extend_global_recovery_throttle_for_spread") as throttle_mock,
            patch.object(app, "save_state") as save_mock,
            patch.object(app, "send_audit_log", new=AsyncMock()) as audit_mock,
        ):
            await app.run_channel_send_as_health_scheduler(now)

        self.assertFalse(state_module.get_identity_enabled(frozen_id))
        health = state_module.get_channel_send_as_health()
        self.assertEqual("closed", health["status"])
        self.assertEqual([frozen_id], health["restore_identity_ids"])
        self.assertGreater(health["next_probe_at"], now)
        self.assertEqual(
            [
                (primary_group_id, frozen_id),
                (primary_group_id, account_id),
                (backup_group_id, frozen_id),
                (backup_group_id, account_id),
            ],
            client.save_default_pairs,
        )
        initialize_mock.assert_not_called()
        spread_mock.assert_not_called()
        throttle_mock.assert_not_called()
        save_mock.assert_not_called()
        audit_mock.assert_not_awaited()

    async def test_probe_partially_restores_only_available_channel_identities(self):
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

        client = _SendAsProbeClient(
            [first_id, second_id],
            saveable_ids=[second_id],
        )
        with (
            patch.object(app, "get_registered_client", return_value=client),
            patch.object(app, "is_account_offline", return_value=False),
            patch.object(app, "initialize_identity_runtime") as initialize_mock,
            patch.object(app, "spread_overdue_runtime_timers", return_value=1),
            patch.object(app, "extend_global_recovery_throttle_for_spread"),
            patch.object(app, "save_state"),
            patch.object(app, "send_audit_log", new=AsyncMock()) as audit_mock,
        ):
            await app.run_channel_send_as_health_scheduler(now)

        self.assertFalse(state_module.get_identity_enabled(first_id))
        self.assertTrue(state_module.get_identity_enabled(second_id))
        health = state_module.get_channel_send_as_health()
        self.assertEqual("closed", health["status"])
        self.assertEqual([first_id], health["restore_identity_ids"])
        self.assertEqual([first_id], health["frozen_identity_ids"])
        initialize_mock.assert_called_once_with(second_id, now)
        self.assertIn("部分恢复", audit_mock.await_args.args[0])
        self.assertEqual([first_id, second_id, account_id], client.save_default_ids)

    async def test_stale_candidates_do_not_restore_when_save_default_rejects_all(self):
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

        client = _SendAsProbeClient(
            [first_id, second_id],
            saveable_ids=[],
        )
        with (
            patch.object(app, "get_registered_client", return_value=client),
            patch.object(app, "is_account_offline", return_value=False),
            patch.object(app, "initialize_identity_runtime") as initialize_mock,
            patch.object(app, "spread_overdue_runtime_timers") as spread_mock,
            patch.object(app, "extend_global_recovery_throttle_for_spread") as throttle_mock,
            patch.object(app, "save_state") as save_mock,
            patch.object(app, "send_audit_log", new=AsyncMock()) as audit_mock,
        ):
            await app.run_channel_send_as_health_scheduler(now)

        self.assertFalse(state_module.get_identity_enabled(first_id))
        self.assertFalse(state_module.get_identity_enabled(second_id))
        health = state_module.get_channel_send_as_health()
        self.assertEqual("closed", health["status"])
        self.assertEqual([first_id, second_id], health["restore_identity_ids"])
        self.assertEqual([first_id, second_id], health["frozen_identity_ids"])
        self.assertEqual("SendAsPeerInvalidError", health["last_error"])
        self.assertGreater(health["next_probe_at"], now)
        self.assertEqual([first_id, second_id, account_id], client.save_default_ids)
        initialize_mock.assert_not_called()
        spread_mock.assert_not_called()
        throttle_mock.assert_not_called()
        save_mock.assert_not_called()
        audit_mock.assert_not_awaited()

    async def test_empty_candidate_list_restores_personal_identity_and_stays_closed(self):
        now = 1_700_000_000.0
        account_id = 301299112
        game_group_id = -1002083016447
        first_id = 3504367852
        state_module.ensure_identity_registered(account_id)
        state_module.ensure_identity_registered(first_id)
        state_module.set_identity_account(account_id, account_id)
        state_module.set_identity_account(first_id, account_id)
        state_module.set_identity_enabled(first_id, False)
        state_module.set_game_group_id(game_group_id)
        state_module.set_channel_send_as_health({
            "status": "closed",
            "account_id": account_id,
            "game_group_id": game_group_id,
            "next_probe_at": now - 1,
            "restore_identity_ids": [first_id],
            "frozen_identity_ids": [first_id],
        })

        client = _SendAsProbeClient([], saveable_ids=[])
        with (
            patch.object(app, "get_registered_client", return_value=client),
            patch.object(app, "is_account_offline", return_value=False),
            patch.object(app, "initialize_identity_runtime") as initialize_mock,
            patch.object(app, "spread_overdue_runtime_timers") as spread_mock,
            patch.object(app, "extend_global_recovery_throttle_for_spread") as throttle_mock,
            patch.object(app, "save_state") as save_mock,
            patch.object(app, "send_audit_log", new=AsyncMock()) as audit_mock,
        ):
            await app.run_channel_send_as_health_scheduler(now)

        health = state_module.get_channel_send_as_health()
        self.assertEqual("closed", health["status"])
        self.assertEqual("SendAsPeerInvalidError", health["last_error"])
        self.assertEqual([account_id], client.save_default_ids)
        initialize_mock.assert_not_called()
        spread_mock.assert_not_called()
        throttle_mock.assert_not_called()
        save_mock.assert_not_called()
        audit_mock.assert_not_awaited()

    async def test_probe_falls_back_to_per_identity_save_default(self):
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

        client = _SendAsProbeClient(
            [second_id],
            saveable_ids=[second_id],
            reject_get_send_as=True,
        )
        with (
            patch.object(app, "get_registered_client", return_value=client),
            patch.object(app, "is_account_offline", return_value=False),
            patch.object(app, "initialize_identity_runtime") as initialize_mock,
            patch.object(app, "spread_overdue_runtime_timers", return_value=1),
            patch.object(app, "extend_global_recovery_throttle_for_spread"),
            patch.object(app, "save_state"),
            patch.object(app, "send_audit_log", new=AsyncMock()),
        ):
            await app.run_channel_send_as_health_scheduler(now)

        self.assertFalse(state_module.get_identity_enabled(first_id))
        self.assertTrue(state_module.get_identity_enabled(second_id))
        self.assertEqual([first_id], state_module.get_channel_send_as_health()["restore_identity_ids"])
        initialize_mock.assert_called_once_with(second_id, now)
        self.assertEqual([first_id, second_id, account_id], client.save_default_ids)


if __name__ == "__main__":
    unittest.main()
