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
        self.cancelled_count = 0

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
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                self.cancelled_count += 1
                raise
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
            copy.deepcopy(runtime._GAME_SEND_BLOCK_LAST),
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
        runtime._GAME_SEND_BLOCK_LAST.clear()
        runtime._GAME_SEND_BLOCK_LAST.update(copy.deepcopy(self._queue_snapshot[5]))
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    async def test_idle_global_slot_sends_without_artificial_wait(self):
        send_as_id = 301299112
        runtime._GAME_LAST_SEND_AT = 10.0
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
            patch.object(runtime.random, "uniform", return_value=18.0),
            patch.object(runtime.asyncio, "sleep", new=fake_sleep),
        ):
            async with runtime._send_slot(
                runtime.SEND_PRIORITY_NORMAL,
                command=".观星台",
                send_as_id=send_as_id,
            ):
                self.assertEqual(100.0, clock["mono"])

        self.assertEqual([], sleeps)

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
                patch.object(runtime, "GAME_SEND_TIMEOUT_RECOVERY_WAIT_SEC", 0.0),
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

    async def test_send_rpc_timeout_recovers_message_id_from_message_log(self):
        send_as_id = 301299112
        account_id = 7001
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        client = _FakeClient(["timeout"])
        recovered = {
            "event_type": "message",
            "message_id": 920002,
            "ts_epoch": 1234.5,
        }

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "GAME_SEND_RPC_TIMEOUT_SEC", 0.05),
                patch.object(runtime, "get_registered_client", return_value=client),
                patch.object(runtime, "is_account_offline", return_value=False),
                patch.object(runtime, "get_game_group_id", return_value=123456),
                patch.object(runtime, "get_game_topic_id", return_value=7310786),
                patch.object(runtime, "get_global_enabled", return_value=True),
                patch.object(runtime, "_get_send_gap_range", return_value=(0.0, 0.0)),
                patch.object(runtime, "_module_send_gap_min_sec", return_value=0.0),
                patch.object(runtime, "IDENTITY_SEND_GAP_MIN_SEC", 0.0),
                patch.object(runtime, "_dungeon_quiet_blocks_send", new=AsyncMock(return_value=False)),
                patch.object(runtime, "is_identity_weak", return_value=False),
                patch.object(runtime, "action_guard_before_send", return_value=(True, "")),
                patch.object(runtime, "recover_sent_command_from_message_log", return_value=recovered),
                patch.object(runtime, "mark_dirty"),
            ):
                stack.enter_context(patcher)
            audit_mock = stack.enter_context(patch.object(runtime, "send_audit_log", new=AsyncMock()))
            append_mock = stack.enter_context(patch.object(runtime, "_append_sent_message_log"))
            guard_note_mock = stack.enter_context(patch.object(runtime, "action_guard_note_sent"))
            note_sent_mock = stack.enter_context(patch.object(runtime, "note_game_command_sent"))
            observer_mock = stack.enter_context(patch.object(runtime, "_notify_game_command_sent_observers"))

            msg = await asyncio.wait_for(
                runtime.send_game_command(".观星台", send_as_id=send_as_id, track=True),
                timeout=1,
            )

        self.assertEqual(920002, msg.id)
        self.assertTrue(msg.recovered_from_message_log)
        append_mock.assert_called_once()
        guard_note_mock.assert_called_once_with(".观星台", send_as_id, 920002, sent_at=1234.5)
        note_sent_mock.assert_called_once_with(".观星台", sent_at=1234.5, priority=runtime.SEND_PRIORITY_NORMAL)
        observer_mock.assert_called_once()
        audit_mock.assert_awaited()
        pending = state_module.get_identity_state(send_as_id)["pending_tasks"][920002]
        self.assertEqual(".观星台", pending["cmd"])
        self.assertEqual(1234.5, pending["sent_at"])

    async def test_slow_rpc_does_not_hold_global_send_lock(self):
        send_as_id = 301299112
        account_id = 7001
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        client = _FakeClient(["timeout", "ok"])

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "GAME_SEND_RPC_TIMEOUT_SEC", 0.35),
                patch.object(runtime, "GAME_SEND_TIMEOUT_RECOVERY_WAIT_SEC", 0.0),
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

            first_task = asyncio.create_task(
                runtime.send_game_command(".慢返回", send_as_id=send_as_id, priority="probe", track=False)
            )
            await asyncio.sleep(0.05)
            second = await asyncio.wait_for(
                runtime.send_game_command(".正常发送", send_as_id=send_as_id, priority="probe", track=False),
                timeout=0.2,
            )
            first = await asyncio.wait_for(first_task, timeout=1)

        self.assertIsNone(first)
        self.assertEqual(910001, second.id)
        self.assertFalse(runtime._GAME_SEND_LOCK.locked())
        self.assertEqual(2, len(client.sent_requests))
        self.assertEqual(0, client.cancelled_count)

    async def test_send_timeout_recovery_polls_for_delayed_message_log_entry(self):
        recovered = {
            "event_type": "message",
            "message_id": 920003,
            "ts_epoch": 5678.5,
        }

        async def fake_sleep(_delay):
            return None

        with (
            patch.object(runtime, "GAME_SEND_TIMEOUT_RECOVERY_WAIT_SEC", 5.0),
            patch.object(runtime.time, "time", return_value=100.0),
            patch.object(runtime.asyncio, "sleep", new=fake_sleep),
            patch.object(runtime, "recover_sent_command_from_message_log", side_effect=[None, None, recovered]) as recover_mock,
        ):
            result = await runtime._recover_timed_out_game_send(
                ".元婴状态",
                send_as_id=301299112,
                send_started_at=99.0,
                game_group_id=123456,
                topic_id=7310786,
            )

        self.assertEqual(920003, result["message_id"])
        self.assertEqual(3, recover_mock.call_count)

    async def test_send_queue_timeout_releases_action_guard_placeholder(self):
        send_as_id = 301299112
        runtime._GAME_LAST_SEND_AT = runtime.time.monotonic()
        state_module.ensure_identity_registered(send_as_id)
        client = _FakeClient(["ok"])
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
                patch.object(runtime, "_get_any_authed_client", return_value=client),
                patch.object(runtime, "_get_send_gap_range", return_value=(10.0, 10.0)),
                patch.object(runtime.random, "uniform", return_value=10.0),
                patch.object(runtime, "_module_send_gap_min_sec", return_value=0.0),
                patch.object(runtime, "IDENTITY_SEND_GAP_MIN_SEC", 0.0),
                patch.object(runtime, "_effective_send_queue_timeout", return_value=0.01),
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

    async def test_effective_queue_timeout_covers_rpc_and_identity_gap(self):
        timeout = runtime._effective_send_queue_timeout(
            runtime.SEND_PRIORITY_REACTIVE,
            command=".天机代卜",
            send_as_id=301299112,
            intent={"source_module": "侍妾"},
            queue_timeout=45,
        )

        self.assertGreaterEqual(
            timeout,
            runtime.GAME_SEND_RPC_TIMEOUT_SEC
            + runtime.GAME_SEND_TIMEOUT_RECOVERY_WAIT_SEC
            + max(runtime.REACTIVE_SEND_GAP_MAX_SEC, runtime.IDENTITY_SEND_GAP_MIN_SEC)
            + runtime.SEND_QUEUE_TIMEOUT_MARGIN_SEC,
        )


if __name__ == "__main__":
    unittest.main()
