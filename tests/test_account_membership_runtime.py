import copy
import unittest
from unittest.mock import AsyncMock, patch

from telethon import errors

from model import app
from model import runtime
from model import state as state_module
from model.account_membership import TargetGroupMembership, TargetGroupMembershipProbe


class AccountMembershipSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        state_module.set_game_group_id(-1002083016447)
        for identity_id in (301299112, 8659059191):
            state_module.ensure_identity_registered(identity_id)
            state_module.set_identity_account(identity_id, 301299112)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    async def test_definitive_negative_freezes_group_commands_without_disabling_identity(self):
        fake_client = object()
        with (
            patch.object(app, "get_accounts", return_value={"301299112": {}}),
            patch.object(app, "get_all_clients", return_value={301299112: fake_client}),
            patch.object(app, "get_registered_client", return_value=fake_client),
            patch.object(app, "is_account_offline", return_value=False),
            patch.object(app, "probe_target_group_membership", new=AsyncMock(return_value=TargetGroupMembershipProbe(
                TargetGroupMembership.NOT_MEMBER,
                "USER_NOT_PARTICIPANT",
                "USER_NOT_PARTICIPANT",
            ))),
            patch.object(app, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(app, "mark_dirty"),
        ):
            results = await app.run_account_target_membership_probe_scheduler(1000.0, force=True)

        self.assertEqual(1, len(results))
        record = state_module.get_account_target_membership(301299112)
        self.assertEqual("not_member", record["status"])
        self.assertEqual([301299112, 8659059191], record["identity_ids"])
        self.assertTrue(state_module.get_identity_enabled(301299112))
        self.assertTrue(state_module.get_identity_enabled(8659059191))
        self.assertTrue(state_module.is_cave_public_identity_available(8659059191))
        audit_mock.assert_awaited_once()

    async def test_transient_recheck_does_not_restore_known_negative(self):
        state_module.set_account_target_membership(301299112, {
            "account_id": 301299112,
            "game_group_id": -1002083016447,
            "identity_ids": [301299112, 8659059191],
            "status": "not_member",
            "probe_status": "not_member",
            "reason": "USER_NOT_PARTICIPANT",
            "checked_at": 100.0,
            "last_definitive_at": 100.0,
            "next_probe_at": 200.0,
        })
        fake_client = object()
        with (
            patch.object(app, "get_accounts", return_value={"301299112": {}}),
            patch.object(app, "get_all_clients", return_value={301299112: fake_client}),
            patch.object(app, "get_registered_client", return_value=fake_client),
            patch.object(app, "is_account_offline", return_value=False),
            patch.object(app, "probe_target_group_membership", new=AsyncMock(return_value=TargetGroupMembershipProbe(
                TargetGroupMembership.UNKNOWN,
                "telegram internal",
                "RPCCALLFAILERROR",
            ))),
            patch.object(app, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(app, "mark_dirty"),
        ):
            await app.run_account_target_membership_probe_scheduler(1000.0, force=True)

        record = state_module.get_account_target_membership(301299112)
        self.assertEqual("not_member", record["status"])
        self.assertEqual("unknown", record["probe_status"])
        audit_mock.assert_not_awaited()

    async def test_member_probe_restores_group_command_eligibility(self):
        state_module.set_account_target_membership(301299112, {
            "account_id": 301299112,
            "game_group_id": -1002083016447,
            "identity_ids": [301299112, 8659059191],
            "status": "not_member",
            "probe_status": "not_member",
            "reason": "USER_NOT_PARTICIPANT",
            "checked_at": 100.0,
            "last_definitive_at": 100.0,
            "next_probe_at": 200.0,
        })
        fake_client = object()
        with (
            patch.object(app, "get_accounts", return_value={"301299112": {}}),
            patch.object(app, "get_all_clients", return_value={301299112: fake_client}),
            patch.object(app, "get_registered_client", return_value=fake_client),
            patch.object(app, "is_account_offline", return_value=False),
            patch.object(app, "probe_target_group_membership", new=AsyncMock(return_value=TargetGroupMembershipProbe(
                TargetGroupMembership.MEMBER,
            ))),
            patch.object(app, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(app, "mark_dirty"),
        ):
            await app.run_account_target_membership_probe_scheduler(1000.0, force=True)

        self.assertEqual("member", state_module.get_account_target_membership(301299112)["status"])
        audit_mock.assert_awaited_once()

    async def test_due_backup_membership_is_reprobed_and_restored(self):
        primary_group_id = -1002083016447
        backup_group_id = -1001680975844
        state_module.set_game_group_route_config({
            "enabled": True,
            "primary_group_id": primary_group_id,
            "backup_group_ids": [backup_group_id],
            "topic_id_by_group": {str(primary_group_id): 0, str(backup_group_id): 7310786},
        })
        primary_record = {
            "account_id": 301299112,
            "game_group_id": primary_group_id,
            "identity_ids": [301299112, 8659059191],
            "status": "member",
            "probe_status": "member",
            "checked_at": 900.0,
            "last_definitive_at": 900.0,
            "next_probe_at": 2000.0,
        }
        state_module.set_account_target_membership(301299112, primary_record)
        state_module.set_account_group_membership(301299112, primary_group_id, primary_record)
        state_module.set_account_group_membership(301299112, backup_group_id, {
            "account_id": 301299112,
            "game_group_id": backup_group_id,
            "identity_ids": [301299112, 8659059191],
            "status": "not_member",
            "probe_status": "not_member",
            "reason": "USER_NOT_PARTICIPANT",
            "checked_at": 100.0,
            "last_definitive_at": 100.0,
            "next_probe_at": 200.0,
        })
        fake_client = object()
        membership_probe = AsyncMock(return_value=TargetGroupMembershipProbe(TargetGroupMembership.MEMBER))
        with (
            patch.object(app, "get_accounts", return_value={"301299112": {}}),
            patch.object(app, "get_all_clients", return_value={301299112: fake_client}),
            patch.object(app, "get_registered_client", return_value=fake_client),
            patch.object(app, "is_account_offline", return_value=False),
            patch.object(app, "probe_target_group_membership", new=membership_probe),
            patch.object(app, "send_audit_log", new=AsyncMock()) as audit_mock,
            patch.object(app, "mark_dirty"),
        ):
            results = await app.run_account_target_membership_probe_scheduler(1000.0)

        self.assertEqual(1, len(results))
        self.assertEqual(backup_group_id, membership_probe.await_args.args[1])
        self.assertEqual(
            "member",
            state_module.get_account_group_membership(301299112, backup_group_id)["status"],
        )
        audit_mock.assert_awaited_once()


class AccountMembershipSendGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._send_lock = runtime._GAME_SEND_LOCK
        self._block_snapshot = copy.deepcopy(runtime._GAME_SEND_BLOCK_LAST)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        state_module.set_game_group_id(-1002083016447)
        state_module.ensure_identity_registered(301299112)
        state_module.set_identity_account(301299112, 301299112)
        runtime._GAME_SEND_LOCK = __import__("asyncio").Lock()
        runtime._GAME_SEND_BLOCK_LAST.clear()

    def tearDown(self):
        runtime._GAME_SEND_LOCK = self._send_lock
        runtime._GAME_SEND_BLOCK_LAST.clear()
        runtime._GAME_SEND_BLOCK_LAST.update(copy.deepcopy(self._block_snapshot))
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    async def test_known_negative_blocks_before_telegram_send(self):
        state_module.set_account_target_membership(301299112, {
            "account_id": 301299112,
            "game_group_id": -1002083016447,
            "identity_ids": [301299112],
            "status": "not_member",
            "probe_status": "not_member",
            "reason": "USER_NOT_PARTICIPANT",
            "next_probe_at": 2000.0,
        })
        client = AsyncMock()
        with (
            patch.object(runtime, "get_registered_client", return_value=client),
            patch.object(runtime, "is_account_offline", return_value=False),
            patch.object(runtime, "get_global_enabled", return_value=True),
            patch.object(runtime, "_dungeon_quiet_blocks_send", new=AsyncMock(return_value=False)),
            patch.object(runtime, "send_audit_log", new=AsyncMock()),
        ):
            result = await runtime.send_game_command(".状态", send_as_id=301299112, priority="probe", track=False)

        self.assertIsNone(result)
        client.assert_not_awaited()
        block = runtime.classify_game_send_block(301299112, ".状态")
        self.assertEqual("account_not_in_target_group", block["code"])
        self.assertEqual("unsent", block["status"])
        self.assertTrue(state_module.get_identity_enabled(301299112))

    async def test_definitive_send_error_persists_negative_gate(self):
        class FailingClient:
            async def get_input_entity(self, entity_id):
                return entity_id

            async def __call__(self, _request):
                raise errors.UserNotParticipantError(request=None)

            def is_connected(self):
                return True

            async def is_user_authorized(self):
                return True

        client = FailingClient()
        with (
            patch.object(runtime, "get_registered_client", return_value=client),
            patch.object(runtime, "is_account_offline", return_value=False),
            patch.object(runtime, "get_global_enabled", return_value=True),
            patch.object(runtime, "get_game_topic_id", return_value=0),
            patch.object(runtime, "_get_send_gap_range", return_value=(0.0, 0.0)),
            patch.object(runtime, "_module_send_gap_min_sec", return_value=0.0),
            patch.object(runtime, "IDENTITY_SEND_GAP_MIN_SEC", 0.0),
            patch.object(runtime, "_dungeon_quiet_blocks_send", new=AsyncMock(return_value=False)),
            patch.object(runtime, "is_identity_weak", return_value=False),
            patch.object(runtime, "action_guard_before_send", return_value=(True, "")),
            patch.object(runtime, "send_audit_log", new=AsyncMock()),
        ):
            result = await runtime.send_game_command(".状态", send_as_id=301299112, priority="probe", track=False)

        self.assertIsNone(result)
        self.assertEqual("not_member", state_module.get_account_target_membership(301299112)["status"])
        self.assertEqual(
            "account_not_in_target_group",
            runtime.classify_game_send_block(301299112, ".状态")["code"],
        )
