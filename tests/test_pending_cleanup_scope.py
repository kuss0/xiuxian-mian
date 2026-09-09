import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from model import runtime
from model import state as state_module
from model.features import _phaseful, deep_retreat, second_soul


class PendingCleanupScopeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        for identity_id in (991301, 991302):
            state_module.ensure_identity_registered(identity_id)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(self.snapshot)

    def pending_pair(self, command):
        for identity_id in (991301, 991302):
            state_module.get_identity_state(identity_id)["pending_tasks"][7001] = {
                "cmd": command, "chat_id": -identity_id, "sent_at": 1.0,
            }

    def assert_only_current_identity_cleared(self):
        self.assertNotIn(7001, state_module.get_identity_state(991301)["pending_tasks"])
        self.assertIn(7001, state_module.get_identity_state(991302)["pending_tasks"])

    async def test_second_soul_timeout_does_not_clear_another_identity(self):
        self.pending_pair(second_soul.CMD_SECOND_SOUL_STATUS)
        state_module.set_identity_account(991301, 7601)
        with (
            state_module.use_identity(991301) as current,
            patch.object(second_soul, "_recover_second_soul_pending_from_message_log", new=AsyncMock(return_value=False)),
            patch.object(second_soul, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7002, sent_at=1000))),
            patch.object(second_soul, "send_audit_log", new=AsyncMock()),
            patch.object(second_soul, "save_state"),
        ):
            current.update(second_soul_enabled=True, second_soul_phase="status_pending", next_second_soul_time=10)
            current["second_soul_commands"] = {"status": {
                "identity_id": 991301, "account_id": 7601, "op_id": "scope-test",
                "command": second_soul.CMD_SECOND_SOUL_STATUS, "chat_id": -991301,
                "msg_id": 7001, "started_at": 1.0, "sent_at": 1.0, "status": "sent",
            }}
            await second_soul.run_second_soul_scheduler(1000)
        self.assert_only_current_identity_cleared()

    async def test_phaseful_calibration_does_not_clear_another_identity(self):
        command = deep_retreat.CMD_DEEP_RETREAT
        self.pending_pair(command)
        spec = deep_retreat.DEEP_RETREAT_SPEC
        now = 10000.0
        with (
            state_module.use_identity(991301) as current,
            patch.object(_phaseful, "_send_active_summary_query", new=AsyncMock(return_value=True)) as query,
            patch.object(_phaseful, "send_audit_log", new=AsyncMock()),
        ):
            current[spec.phase_key] = "launching"
            current[spec.last_command_key] = now - spec.launching_timeout_sec - 1
            self.assertTrue(await _phaseful._calibrate_launching_timeout_once(spec, now, command))
        query.assert_awaited_once_with(spec, now)
        self.assert_only_current_identity_cleared()

    def test_cleanup_requires_explicit_scope(self):
        with self.assertRaises(TypeError):
            runtime.clear_pending_tasks_by_commands({".test"})

    def test_explicit_global_cleanup_remains_available(self):
        self.pending_pair(".test")
        runtime.clear_pending_tasks_by_commands({".test"}, send_as_id=None)
        for identity_id in (991301, 991302):
            self.assertFalse(state_module.get_identity_state(identity_id)["pending_tasks"])
