import asyncio
import copy
import sys
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import runtime
from model import state as state_module


class _FakeClient:
    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.sent_requests = []

    def is_connected(self):
        return True

    async def is_user_authorized(self):
        return True

    async def get_input_entity(self, entity_id):
        return SimpleNamespace(id=int(entity_id or 0))

    async def get_dialogs(self):
        return []

    async def __call__(self, request):
        self.sent_requests.append(request)
        behavior = self.behaviors.pop(0) if self.behaviors else "ok"
        if behavior == "timeout":
            await asyncio.sleep(10)
        return SimpleNamespace(id=910001)


class RuntimeSendTimeoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._queue_snapshot = (
            runtime._GAME_LAST_SEND_AT,
            copy.deepcopy(runtime._MODULE_LAST_SEND_AT),
            copy.deepcopy(runtime._IDENTITY_LAST_SEND_AT),
            runtime._GAME_SEND_QUEUE_SEQ,
            copy.deepcopy(runtime._GAME_SEND_QUEUE_ITEMS),
        )
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module._meta_state["identity_account_map"] = {}

    def tearDown(self):
        runtime._GAME_LAST_SEND_AT = self._queue_snapshot[0]
        runtime._MODULE_LAST_SEND_AT.clear()
        runtime._MODULE_LAST_SEND_AT.update(self._queue_snapshot[1])
        runtime._IDENTITY_LAST_SEND_AT.clear()
        runtime._IDENTITY_LAST_SEND_AT.update(self._queue_snapshot[2])
        runtime._GAME_SEND_QUEUE_SEQ = self._queue_snapshot[3]
        runtime._GAME_SEND_QUEUE_ITEMS.clear()
        runtime._GAME_SEND_QUEUE_ITEMS.update(copy.deepcopy(self._queue_snapshot[4]))
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    async def test_identity_gap_applies_after_whitelisted_burst_send(self):
        send_as_id = 301299112
        runtime._GAME_LAST_SEND_AT = 0.0
        runtime._MODULE_LAST_SEND_AT.clear()
        runtime._IDENTITY_LAST_SEND_AT.clear()
        sleeps = []
        clock = {"mono": 100.0}

        async def fake_sleep(delay):
            delay = float(delay or 0)
            sleeps.append(delay)
            clock["mono"] += delay

        with (
            patch.object(runtime.time, "monotonic", side_effect=lambda: clock["mono"]),
            patch.object(runtime.random, "uniform", return_value=0.0),
            patch.object(runtime, "_get_send_gap_range", return_value=(0.0, 0.0)),
            patch.object(runtime.asyncio, "sleep", new=fake_sleep),
        ):
            async with runtime._send_slot(
                runtime.SEND_PRIORITY_EVENT_BURST,
                command=".提竿",
                send_as_id=send_as_id,
                intent={"source_module": "灵溪垂钓"},
            ):
                self.assertEqual(100.0, clock["mono"])

            self.assertEqual(0.0, runtime._GAME_LAST_SEND_AT)
            self.assertEqual(100.0, runtime._IDENTITY_LAST_SEND_AT[send_as_id])

            async with runtime._send_slot(
                runtime.SEND_PRIORITY_NORMAL,
                command=".定命 太阴",
                send_as_id=send_as_id,
                intent={"source_module": "天星宗"},
            ):
                self.assertEqual(110.0, clock["mono"])

        self.assertGreaterEqual(sum(sleeps), runtime.IDENTITY_SEND_GAP_MIN_SEC)

    async def test_send_rpc_timeout_releases_global_send_lock(self):
        send_as_id = 301299112
        account_id = 7001
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        client = _FakeClient(["timeout", "ok"])

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "GAME_SEND_RPC_TIMEOUT_SEC", 0.05),
                patch.object(runtime, "get_registered_client", return_value=client),
                patch.object(runtime, "is_account_offline", return_value=False),
                patch.object(runtime, "get_game_group_id", return_value=123456),
                patch.object(runtime, "get_game_topic_id", return_value=0),
                patch.object(runtime, "get_global_enabled", return_value=True),
                patch.object(runtime, "_get_send_gap_range", return_value=(0.0, 0.0)),
                patch.object(runtime, "_module_send_gap_min_sec", return_value=0.0),
                patch.object(runtime, "IDENTITY_SEND_GAP_MIN_SEC", 0.0),
                patch.object(runtime, "_dungeon_quiet_blocks_send", new=AsyncMock(return_value=False)),
                patch.object(runtime, "is_identity_weak", return_value=False),
                patch.object(runtime, "action_guard_before_send", return_value=(True, "")),
                patch.object(runtime, "send_audit_log", new=AsyncMock()),
                patch.object(runtime, "_append_sent_message_log"),
                patch.object(runtime, "action_guard_note_sent"),
                patch.object(runtime, "mark_dirty"),
                patch.object(runtime, "note_game_command_sent"),
                patch.object(runtime, "_notify_game_command_sent_observers"),
            ):
                stack.enter_context(patcher)
            first = await asyncio.wait_for(
                runtime.send_game_command(".测试超时", send_as_id=send_as_id, priority="probe", track=False),
                timeout=1,
            )
            self.assertIsNone(first)
            self.assertFalse(runtime._GAME_SEND_LOCK.locked())

            second = await asyncio.wait_for(
                runtime.send_game_command(".测试恢复", send_as_id=send_as_id, priority="probe", track=False),
                timeout=1,
            )

        self.assertEqual(910001, second.id)
        self.assertFalse(runtime._GAME_SEND_LOCK.locked())
        self.assertEqual(2, len(client.sent_requests))

    async def test_send_queue_timeout_releases_action_guard_placeholder(self):
        send_as_id = 301299112
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["action_guard_sessions"] = {
                "wild_training": {
                    "action_key": "wild_training",
                    "kind": "high_risk",
                    "label": "野外历练",
                    "attempt": 0,
                    "first_sent_at": 0,
                    "last_sent_at": 0,
                    "next_allowed_at": 0,
                    "last_msg_id": 0,
                    "last_command": ".野外历练 谨慎",
                }
            }

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "get_game_group_id", return_value=123456),
                patch.object(runtime, "get_game_topic_id", return_value=0),
                patch.object(runtime, "get_global_enabled", return_value=True),
                patch.object(runtime, "_get_send_gap_range", return_value=(10.0, 10.0)),
                patch.object(runtime, "_module_send_gap_min_sec", return_value=0.0),
                patch.object(runtime, "IDENTITY_SEND_GAP_MIN_SEC", 0.0),
                patch.object(runtime, "_dungeon_quiet_blocks_send", new=AsyncMock(return_value=False)),
                patch.object(runtime, "is_identity_weak", return_value=False),
                patch.object(runtime, "action_guard_before_send", return_value=(True, "")),
                patch.object(runtime, "send_audit_log", new=AsyncMock()),
            ):
                stack.enter_context(patcher)

            result = await asyncio.wait_for(
                runtime.send_game_command(
                    ".野外历练 谨慎",
                    send_as_id=send_as_id,
                    priority="normal",
                    track=False,
                    queue_timeout=0.01,
                ),
                timeout=1,
            )

        self.assertIsNone(result)
        self.assertFalse(runtime._GAME_SEND_LOCK.locked())
        self.assertNotIn("wild_training", state_module.get_identity_state(send_as_id)["action_guard_sessions"])
        self.assertEqual(
            "send_queue_timeout",
            runtime.get_last_game_send_block(send_as_id, ".野外历练 谨慎")["code"],
        )


if __name__ == "__main__":
    unittest.main()
