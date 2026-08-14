import asyncio
import copy
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import runtime
from model import message_log_recovery
from model import state as state_module


class _FakeClient:
    def __init__(self, behaviors, *, entity_delay=0.0, entity_timeout=False, send_delay=0.0):
        self.behaviors = list(behaviors)
        self.sent_requests = []
        self.cancelled_count = 0
        self.entity_delay = float(entity_delay or 0.0)
        self.entity_timeout = bool(entity_timeout)
        self.send_delay = float(send_delay or 0.0)
        self.active_entity_requests = 0
        self.max_active_entity_requests = 0

    def is_connected(self):
        return True

    async def is_user_authorized(self):
        return True

    async def get_input_entity(self, entity_id):
        self.active_entity_requests += 1
        self.max_active_entity_requests = max(self.max_active_entity_requests, self.active_entity_requests)
        try:
            if self.entity_timeout:
                await asyncio.sleep(10)
            elif self.entity_delay > 0:
                await asyncio.sleep(self.entity_delay)
        finally:
            self.active_entity_requests -= 1
        return SimpleNamespace(id=int(entity_id or 0))

    async def get_dialogs(self):
        return []

    async def __call__(self, request):
        self.sent_requests.append(request)
        behavior = self.behaviors.pop(0) if self.behaviors else "ok"
        if isinstance(behavior, BaseException):
            raise behavior
        if behavior == "timeout":
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                self.cancelled_count += 1
                raise
        if behavior == "delayed_ok":
            try:
                await asyncio.sleep(self.send_delay)
            except asyncio.CancelledError:
                self.cancelled_count += 1
                raise
        return SimpleNamespace(id=910001)


class RuntimeSendTimeoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._queue_snapshot = (
            runtime._GAME_SEND_LOCK,
            runtime._GAME_LAST_SEND_AT,
            copy.deepcopy(runtime._MODULE_LAST_SEND_AT),
            copy.deepcopy(runtime._IDENTITY_LAST_SEND_AT),
            runtime._GAME_SEND_QUEUE_SEQ,
            copy.deepcopy(runtime._GAME_SEND_QUEUE_ITEMS),
            copy.deepcopy(runtime._GAME_SEND_BLOCK_LAST),
            copy.deepcopy(runtime._SEND_AS_PEER_INVALID_UNTIL),
            copy.deepcopy(runtime._CHANNEL_SEND_AS_INVALID_UNTIL),
            copy.deepcopy(runtime._CHANNEL_SEND_AS_INVALID_OBSERVATIONS),
            dict(runtime._ACCOUNT_RPC_LOCKS),
            runtime.is_game_send_quiesced(),
        )
        runtime._GAME_SEND_LOCK = asyncio.Lock()
        runtime._ACCOUNT_RPC_LOCKS.clear()
        runtime.set_game_send_quiesced(False)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module._meta_state["identity_account_map"] = {}
        runtime._CHANNEL_SEND_AS_INVALID_OBSERVATIONS.clear()

    def tearDown(self):
        runtime._GAME_SEND_LOCK = self._queue_snapshot[0]
        runtime._GAME_LAST_SEND_AT = self._queue_snapshot[1]
        runtime._MODULE_LAST_SEND_AT.clear()
        runtime._MODULE_LAST_SEND_AT.update(self._queue_snapshot[2])
        runtime._IDENTITY_LAST_SEND_AT.clear()
        runtime._IDENTITY_LAST_SEND_AT.update(self._queue_snapshot[3])
        runtime._GAME_SEND_QUEUE_SEQ = self._queue_snapshot[4]
        runtime._GAME_SEND_QUEUE_ITEMS.clear()
        runtime._GAME_SEND_QUEUE_ITEMS.update(copy.deepcopy(self._queue_snapshot[5]))
        runtime._GAME_SEND_BLOCK_LAST.clear()
        runtime._GAME_SEND_BLOCK_LAST.update(copy.deepcopy(self._queue_snapshot[6]))
        runtime._SEND_AS_PEER_INVALID_UNTIL.clear()
        runtime._SEND_AS_PEER_INVALID_UNTIL.update(copy.deepcopy(self._queue_snapshot[7]))
        runtime._CHANNEL_SEND_AS_INVALID_UNTIL.clear()
        runtime._CHANNEL_SEND_AS_INVALID_UNTIL.update(copy.deepcopy(self._queue_snapshot[8]))
        runtime._CHANNEL_SEND_AS_INVALID_OBSERVATIONS.clear()
        runtime._CHANNEL_SEND_AS_INVALID_OBSERVATIONS.update(copy.deepcopy(self._queue_snapshot[9]))
        runtime._ACCOUNT_RPC_LOCKS.clear()
        runtime._ACCOUNT_RPC_LOCKS.update(self._queue_snapshot[10])
        runtime.set_game_send_quiesced(self._queue_snapshot[11])
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    async def test_supervisor_quiesce_blocks_new_game_commands_as_unsent(self):
        send_as_id = 301299112
        state_module.ensure_identity_registered(send_as_id)
        state_module.state["global_enabled"] = True
        runtime.set_game_send_quiesced(True)

        result = await runtime.send_game_command(".观星台", track=False, send_as_id=send_as_id)

        self.assertIsNone(result)
        block = runtime.classify_game_send_block(send_as_id, ".观星台")
        self.assertEqual("supervisor_quiesce", block["code"])
        self.assertEqual("unsent", block["status"])

    async def test_unbound_identity_never_falls_back_to_another_account(self):
        send_as_id = 301299112
        state_module.ensure_identity_registered(send_as_id)
        state_module.state["global_enabled"] = True

        with (
            patch.object(runtime, "_get_any_authed_client_with_account") as fallback_mock,
            patch.object(runtime, "_log_identity_unbound_blocked", new=AsyncMock()) as log_mock,
            patch.object(runtime, "_close_guard_for_unsent_command") as close_guard_mock,
        ):
            result = await runtime.send_game_command(".观星台", track=False, send_as_id=send_as_id)

        self.assertIsNone(result)
        fallback_mock.assert_not_called()
        log_mock.assert_awaited_once_with(".观星台", send_as_id=send_as_id)
        close_guard_mock.assert_called_once_with(".观星台", send_as_id, "account_unbound")
        block = runtime.classify_game_send_block(send_as_id, ".观星台")
        self.assertEqual("account_unbound", block["code"])
        self.assertEqual("unsent", block["status"])

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

    def test_recovery_queue_keeps_fifo_turn_for_throttled_sends(self):
        runtime._GAME_SEND_QUEUE_ITEMS.update({
            10: {"recovery_ordered": True, "status": "waiting"},
            11: {"recovery_ordered": True, "status": "waiting"},
            12: {"recovery_ordered": False, "status": "waiting"},
        })

        self.assertTrue(runtime._is_recovery_queue_turn(10, recovery_ordered=True))
        self.assertFalse(runtime._is_recovery_queue_turn(11, recovery_ordered=True))
        self.assertTrue(runtime._is_recovery_queue_turn(12, recovery_ordered=False))

        runtime._GAME_SEND_QUEUE_ITEMS.pop(10)
        self.assertTrue(runtime._is_recovery_queue_turn(11, recovery_ordered=True))

    async def test_recovery_send_slot_does_not_let_later_waiter_overtake(self):
        runtime._GAME_LAST_SEND_AT = 0.0
        runtime._GAME_SEND_QUEUE_ITEMS.clear()
        await runtime._GAME_SEND_LOCK.acquire()
        entered = []

        async def worker(label):
            async with runtime._send_slot(
                runtime.SEND_PRIORITY_NORMAL,
                command=f".{label}",
                send_as_id=301299112,
                queue_timeout=1,
            ):
                entered.append(label)

        with (
            patch.object(runtime, "_global_recovery_throttle_active", return_value=True),
            patch.object(runtime, "_get_send_gap_range", return_value=(0.0, 0.0)),
            patch.object(runtime, "IDENTITY_SEND_GAP_MIN_SEC", 0.0),
        ):
            first = asyncio.create_task(worker("first"))
            await asyncio.sleep(0)
            second = asyncio.create_task(worker("second"))
            await asyncio.sleep(0)
            runtime._GAME_SEND_LOCK.release()
            await asyncio.gather(first, second)

        self.assertEqual(["first", "second"], entered)

    def test_append_sent_message_log_uses_actual_sent_at(self):
        sent_at = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.object(runtime, "MESSAGES_DIR", tmpdir), \
                patch.object(runtime, "cleanup_message_logs"), \
                patch.object(runtime, "get_game_group_id", return_value=123456), \
                patch.object(runtime, "get_game_topic_id", return_value=7310786):
            runtime._append_sent_message_log(
                920001,
                ".闯塔",
                301299112,
                priority=runtime.SEND_PRIORITY_NORMAL,
                sent_at=sent_at,
            )
            files = list(Path(tmpdir).glob("*.log"))
            self.assertEqual(1, len(files))
            payload = json.loads(files[0].read_text(encoding="utf-8").strip())

        self.assertEqual("2023-11-15 06:13:20 UTC+8", payload["ts"])
        self.assertEqual(920001, payload["message_id"])
        self.assertEqual(".闯塔", payload["text"])

    def test_append_sent_message_log_uses_actual_route(self):
        sent_at = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.object(runtime, "MESSAGES_DIR", tmpdir), \
                patch.object(runtime, "cleanup_message_logs"):
            runtime._append_sent_message_log(
                920002,
                ".天机盘",
                301299112,
                sent_at=sent_at,
                game_group_id=-1001680975844,
                topic_id=7310786,
            )
            payload = json.loads(next(Path(tmpdir).glob("*.log")).read_text(encoding="utf-8").strip())

        self.assertEqual(-1001680975844, payload["chat_id"])
        self.assertEqual(7310786, payload["topic_id"])

    async def test_identity_gap_applies_after_regular_event_send(self):
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

            self.assertEqual(100.0, runtime._GAME_LAST_SEND_AT)
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

    async def test_send_as_peer_invalid_only_backs_off_the_failing_identity(self):
        send_as_id = 301299112
        sibling_send_as_id = 301299113
        account_id = 7001
        state_module.ensure_identity_registered(send_as_id)
        state_module.ensure_identity_registered(sibling_send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        state_module.set_identity_account(sibling_send_as_id, account_id)
        client = _FakeClient([runtime.SendAsPeerInvalidError(request=None), "ok"])

        with ExitStack() as stack:
            for patcher in (
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
            ):
                stack.enter_context(patcher)

            first = await runtime.send_game_command(
                ".野外历练 谨慎",
                send_as_id=send_as_id,
                priority="probe",
                track=False,
            )
            second = await runtime.send_game_command(
                ".天机盘",
                send_as_id=sibling_send_as_id,
                priority="probe",
                track=False,
            )

        self.assertIsNone(first)
        self.assertEqual(910001, second.id)
        self.assertEqual(2, len(client.sent_requests))
        block = runtime.classify_game_send_block(send_as_id, ".野外历练 谨慎")
        self.assertEqual("send_as_peer_invalid", block["code"])
        self.assertEqual("unsent", block["status"])
        self.assertTrue(block["definitely_unsent"])
        self.assertGreater(block["blocked_until"], runtime.time.time())
        self.assertNotEqual(
            "send_as_peer_invalid",
            runtime.classify_game_send_block(sibling_send_as_id, ".天机盘")["code"],
        )
        self.assertTrue(state_module.get_identity_enabled(send_as_id))
        self.assertTrue(state_module.get_identity_enabled(sibling_send_as_id))
        health = state_module.get_channel_send_as_health()
        self.assertNotEqual("closed", health.get("status"))

    async def test_primary_definitive_send_as_failure_fails_over_once_to_backup(self):
        send_as_id = 301299112
        account_id = 7001
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        state_module.set_game_group_id(-1002083016447)
        state_module.set_game_group_route_config({
            "enabled": True,
            "primary_group_id": -1002083016447,
            "backup_group_ids": [-1001680975844],
            "topic_id_by_group": {"-1002083016447": 0, "-1001680975844": 7310786},
        })
        client = _FakeClient([runtime.SendAsPeerInvalidError(request=None), "ok"])

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "get_registered_client", return_value=client),
                patch.object(runtime, "is_account_offline", return_value=False),
                patch.object(runtime, "get_global_enabled", return_value=True),
                patch.object(runtime, "_get_send_gap_range", return_value=(0.0, 0.0)),
                patch.object(runtime, "_module_send_gap_min_sec", return_value=0.0),
                patch.object(runtime, "IDENTITY_SEND_GAP_MIN_SEC", 0.0),
                patch.object(runtime, "_dungeon_quiet_blocks_send", new=AsyncMock(return_value=False)),
                patch.object(runtime, "is_identity_weak", return_value=False),
                patch.object(runtime, "action_guard_before_send", return_value=(True, "")),
                patch.object(runtime, "send_audit_log", new=AsyncMock()),
            ):
                stack.enter_context(patcher)
            result = await runtime.send_game_command(
                ".天机盘", send_as_id=send_as_id, priority="probe", track=False
            )

        self.assertEqual(910001, result.id)
        self.assertEqual(2, len(client.sent_requests))
        self.assertEqual(-1001680975844, int(client.sent_requests[-1].peer.id))

    async def test_repeated_primary_send_as_failures_back_off_account_cohort_to_backup(self):
        account_id = 7001
        identity_ids = [301299111, 301299112, 301299113]
        primary_group_id = -1002083016447
        backup_group_id = -1001680975844
        for identity_id in identity_ids:
            state_module.ensure_identity_registered(identity_id)
            state_module.set_identity_account(identity_id, account_id)
        state_module.set_game_group_id(primary_group_id)
        state_module.set_game_group_route_config({
            "enabled": True,
            "primary_group_id": primary_group_id,
            "backup_group_ids": [backup_group_id],
            "topic_id_by_group": {str(primary_group_id): 0, str(backup_group_id): 7310786},
        })
        client = _FakeClient([
            runtime.SendAsPeerInvalidError(request=None), "ok",
            runtime.SendAsPeerInvalidError(request=None), "ok",
            runtime.SendAsPeerInvalidError(request=None), "ok",
            "ok",
        ])

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "get_registered_client", return_value=client),
                patch.object(runtime, "is_account_offline", return_value=False),
                patch.object(runtime, "get_global_enabled", return_value=True),
                patch.object(runtime, "_get_send_gap_range", return_value=(0.0, 0.0)),
                patch.object(runtime, "_module_send_gap_min_sec", return_value=0.0),
                patch.object(runtime, "IDENTITY_SEND_GAP_MIN_SEC", 0.0),
                patch.object(runtime, "_dungeon_quiet_blocks_send", new=AsyncMock(return_value=False)),
                patch.object(runtime, "is_identity_weak", return_value=False),
                patch.object(runtime, "action_guard_before_send", return_value=(True, "")),
                patch.object(runtime, "send_audit_log", new=AsyncMock()),
            ):
                stack.enter_context(patcher)
            for identity_id in identity_ids:
                result = await runtime.send_game_command(
                    ".天机盘", send_as_id=identity_id, priority="probe", track=False
                )
                self.assertIsNotNone(result)
            sibling_id = 301299114
            state_module.ensure_identity_registered(sibling_id)
            state_module.set_identity_account(sibling_id, account_id)
            result = await runtime.send_game_command(
                ".观命", send_as_id=sibling_id, priority="probe", track=False
            )

        self.assertIsNotNone(result)
        self.assertEqual(7, len(client.sent_requests))
        self.assertEqual(backup_group_id, int(client.sent_requests[-1].peer.id))
        self.assertNotEqual("closed", state_module.get_channel_send_as_health().get("status"))

    async def test_all_route_cohort_failures_freeze_channel_identities(self):
        account_id = 7001
        identity_ids = [301299111, 301299112, 301299113]
        primary_group_id = -1002083016447
        backup_group_id = -1001680975844
        for identity_id in [account_id, *identity_ids]:
            state_module.ensure_identity_registered(identity_id)
            state_module.set_identity_account(identity_id, account_id)
        state_module.set_game_group_id(primary_group_id)
        state_module.set_game_group_route_config({
            "enabled": True,
            "primary_group_id": primary_group_id,
            "backup_group_ids": [backup_group_id],
            "topic_id_by_group": {str(primary_group_id): 0, str(backup_group_id): 7310786},
        })
        client = _FakeClient([
            runtime.SendAsPeerInvalidError(request=None),
            runtime.SendAsPeerInvalidError(request=None),
        ] * len(identity_ids))

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "get_registered_client", return_value=client),
                patch.object(runtime, "is_account_offline", return_value=False),
                patch.object(runtime, "get_global_enabled", return_value=True),
                patch.object(runtime, "_get_send_gap_range", return_value=(0.0, 0.0)),
                patch.object(runtime, "_module_send_gap_min_sec", return_value=0.0),
                patch.object(runtime, "IDENTITY_SEND_GAP_MIN_SEC", 0.0),
                patch.object(runtime, "_dungeon_quiet_blocks_send", new=AsyncMock(return_value=False)),
                patch.object(runtime, "is_identity_weak", return_value=False),
                patch.object(runtime, "action_guard_before_send", return_value=(True, "")),
                patch.object(runtime, "send_audit_log", new=AsyncMock()),
            ):
                stack.enter_context(patcher)
            for identity_id in identity_ids:
                result = await runtime.send_game_command(
                    ".天机盘", send_as_id=identity_id, priority="probe", track=False
                )
                self.assertIsNone(result)

        health = state_module.get_channel_send_as_health()
        self.assertEqual("closed", health["status"])
        self.assertEqual(primary_group_id, health["game_group_id"])
        self.assertEqual(identity_ids, health["restore_identity_ids"])
        self.assertEqual(identity_ids, health["frozen_identity_ids"])
        self.assertTrue(all(not state_module.get_identity_enabled(i) for i in identity_ids))
        self.assertEqual(2 * len(identity_ids), len(client.sent_requests))

    async def test_all_routes_in_send_as_backoff_are_not_reported_as_not_member(self):
        send_as_id = 301299112
        account_id = 7001
        primary_group_id = -1002083016447
        backup_group_id = -1001680975844
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        state_module.set_game_group_id(primary_group_id)
        state_module.set_game_group_route_config({
            "enabled": True,
            "primary_group_id": primary_group_id,
            "backup_group_ids": [backup_group_id],
            "topic_id_by_group": {str(primary_group_id): 0, str(backup_group_id): 7310786},
        })
        now = runtime.time.time()
        runtime._SEND_AS_PEER_INVALID_UNTIL[(send_as_id, primary_group_id)] = now + 1800
        runtime._SEND_AS_PEER_INVALID_UNTIL[(send_as_id, backup_group_id)] = now + 1800

        with patch.object(runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
            blocked = await runtime._account_target_group_blocks_send(
                ".引道 水",
                send_as_id=send_as_id,
                account_id=account_id,
            )

        self.assertTrue(blocked)
        send_block = runtime.classify_game_send_block(send_as_id, ".引道 水")
        self.assertEqual("send_as_peer_invalid", send_block["code"])
        self.assertEqual("unsent", send_block["status"])
        self.assertGreater(send_block["blocked_until"], now)
        audit_mock.assert_not_awaited()

    async def test_primary_timeout_does_not_fail_over(self):
        send_as_id = 301299112
        account_id = 7001
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        state_module.set_game_group_id(-1002083016447)
        state_module.set_game_group_route_config({
            "enabled": True,
            "primary_group_id": -1002083016447,
            "backup_group_ids": [-1001680975844],
            "topic_id_by_group": {"-1002083016447": 0, "-1001680975844": 7310786},
        })
        client = _FakeClient(["timeout"])

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "GAME_SEND_RPC_TIMEOUT_SEC", 0.02),
                patch.object(runtime, "GAME_SEND_TIMEOUT_RECOVERY_WAIT_SEC", 0.0),
                patch.object(runtime, "get_registered_client", return_value=client),
                patch.object(runtime, "is_account_offline", return_value=False),
                patch.object(runtime, "get_global_enabled", return_value=True),
                patch.object(runtime, "_get_send_gap_range", return_value=(0.0, 0.0)),
                patch.object(runtime, "_module_send_gap_min_sec", return_value=0.0),
                patch.object(runtime, "IDENTITY_SEND_GAP_MIN_SEC", 0.0),
                patch.object(runtime, "_dungeon_quiet_blocks_send", new=AsyncMock(return_value=False)),
                patch.object(runtime, "is_identity_weak", return_value=False),
                patch.object(runtime, "action_guard_before_send", return_value=(True, "")),
                patch.object(runtime, "send_audit_log", new=AsyncMock()),
            ):
                stack.enter_context(patcher)
            result = await runtime.send_game_command(
                ".天机盘", send_as_id=send_as_id, priority="probe", track=False
            )

        self.assertIsNone(result)
        self.assertEqual(1, len(client.sent_requests))

    async def test_distinct_send_as_failures_close_the_whole_channel(self):
        account_id = 7001
        identity_ids = [301299111, 301299112, 301299113]
        for identity_id in [account_id, *identity_ids]:
            state_module.ensure_identity_registered(identity_id)
            state_module.set_identity_account(identity_id, account_id)
        client = _FakeClient([
            runtime.SendAsPeerInvalidError(request=None),
            runtime.SendAsPeerInvalidError(request=None),
            runtime.SendAsPeerInvalidError(request=None),
        ])

        with ExitStack() as stack:
            for patcher in (
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
            ):
                stack.enter_context(patcher)
            for identity_id in identity_ids:
                result = await runtime.send_game_command(
                    ".天机盘",
                    send_as_id=identity_id,
                    priority="probe",
                    track=False,
                )
                self.assertIsNone(result)

        health = state_module.get_channel_send_as_health()
        self.assertEqual("closed", health["status"])
        self.assertEqual(account_id, health["account_id"])
        self.assertEqual(identity_ids, health["restore_identity_ids"])
        self.assertTrue(all(not state_module.get_identity_enabled(i) for i in identity_ids))

    async def test_send_as_peer_invalid_text_variant_is_definitely_unsent(self):
        send_as_id = 301299112
        account_id = 7001
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        client = _FakeClient([RuntimeError("You can't send messages as the specified peer")])

        with ExitStack() as stack:
            for patcher in (
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
            ):
                stack.enter_context(patcher)

            result = await runtime.send_game_command(
                ".天机盘",
                send_as_id=send_as_id,
                priority="probe",
                track=False,
            )

        self.assertIsNone(result)
        block = runtime.classify_game_send_block(send_as_id, ".天机盘")
        self.assertEqual("send_as_peer_invalid", block["code"])
        self.assertEqual("unsent", block["status"])
        self.assertTrue(block["definitely_unsent"])

    async def test_successful_send_clears_send_as_peer_invalid_backoff(self):
        send_as_id = 301299112
        account_id = 7001
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        runtime._SEND_AS_PEER_INVALID_UNTIL[(send_as_id, 123456)] = runtime.time.time() + 1800
        runtime._CHANNEL_SEND_AS_INVALID_UNTIL[(account_id, 123456)] = runtime.time.time() + 1800
        client = _FakeClient(["ok"])

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "get_registered_client", return_value=client),
                patch.object(runtime, "is_account_offline", return_value=False),
                patch.object(runtime, "get_game_group_id", return_value=123456),
                patch.object(runtime, "get_game_topic_id", return_value=0),
                patch.object(runtime, "get_global_enabled", return_value=True),
                patch.object(runtime, "_send_as_peer_invalid_until", return_value=0.0),
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

            result = await runtime.send_game_command(
                ".测试恢复",
                send_as_id=send_as_id,
                priority="probe",
                track=False,
            )

        self.assertEqual(910001, result.id)
        self.assertNotIn((send_as_id, 123456), runtime._SEND_AS_PEER_INVALID_UNTIL)
        self.assertNotIn((account_id, 123456), runtime._CHANNEL_SEND_AS_INVALID_UNTIL)

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
        note_sent_mock.assert_called_once_with(
            ".观星台",
            sent_at=1234.5,
            priority=runtime.SEND_PRIORITY_NORMAL,
            msg_id=920002,
        )
        observer_mock.assert_called_once()
        audit_mock.assert_awaited()
        pending = state_module.get_identity_state(send_as_id)["pending_tasks"][920002]
        self.assertEqual(".观星台", pending["cmd"])
        self.assertEqual(1234.5, pending["sent_at"])

    async def test_slow_rpc_holds_global_send_lock_until_resolved(self):
        send_as_id = 301299112
        account_id = 7001
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        client = _FakeClient(["timeout", "ok"], entity_delay=0.2)

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "GAME_SEND_RPC_TIMEOUT_SEC", 0.8),
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
            for _ in range(50):
                if client.active_entity_requests > 0:
                    break
                await asyncio.sleep(0.01)
            self.assertGreater(client.active_entity_requests, 0)
            second_task = asyncio.create_task(
                runtime.send_game_command(".正常发送", send_as_id=send_as_id, priority="probe", track=False)
            )
            await asyncio.sleep(0.05)
            self.assertFalse(second_task.done())
            self.assertEqual(1, client.max_active_entity_requests)
            second_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await second_task
            first = await asyncio.wait_for(first_task, timeout=2)

        self.assertIsNone(first)
        self.assertFalse(runtime._GAME_SEND_LOCK.locked())
        self.assertEqual(1, len(client.sent_requests))
        self.assertEqual(0, client.cancelled_count)

    async def test_send_rpc_timeout_keeps_underlying_send_and_recovers_rpc_result(self):
        send_as_id = 301299112
        account_id = 7001
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        client = _FakeClient(["delayed_ok"], send_delay=0.04)

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "GAME_SEND_RPC_TIMEOUT_SEC", 0.03),
                patch.object(runtime, "GAME_SEND_TIMEOUT_RECOVERY_WAIT_SEC", 0.3),
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

            msg = await asyncio.wait_for(
                runtime.send_game_command(".慢返回可恢复", send_as_id=send_as_id, priority="probe", track=False),
                timeout=1,
            )

        self.assertEqual(910001, msg.id)
        self.assertTrue(msg.recovered_from_message_log)
        self.assertEqual(0, client.cancelled_count)

    async def test_prepare_rpc_is_serialized_by_global_send_slot(self):
        send_as_id = 301299112
        account_id = 7001
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        client = _FakeClient(["ok", "ok"], entity_delay=0.05)

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "GAME_SEND_RPC_TIMEOUT_SEC", 1.0),
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
                runtime.send_game_command(".第一条", send_as_id=send_as_id, priority="probe", track=False)
            )
            second_task = asyncio.create_task(
                runtime.send_game_command(".第二条", send_as_id=send_as_id, priority="probe", track=False)
            )
            first, second = await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=2)

        self.assertEqual(910001, first.id)
        self.assertEqual(910001, second.id)
        self.assertEqual(1, client.max_active_entity_requests)
        self.assertEqual(2, len(client.sent_requests))

    async def test_prepare_timeout_is_not_reported_as_unknown_sent_state(self):
        send_as_id = 301299112
        account_id = 7001
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        client = _FakeClient(["ok"], entity_timeout=True)

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
            ):
                stack.enter_context(patcher)
            audit_mock = stack.enter_context(patch.object(runtime, "send_audit_log", new=AsyncMock()))
            close_guard_mock = stack.enter_context(patch.object(runtime, "_close_guard_for_unsent_command"))

            result = await asyncio.wait_for(
                runtime.send_game_command(".准备超时", send_as_id=send_as_id, priority="probe", track=False),
                timeout=1,
            )

        self.assertIsNone(result)
        self.assertEqual([], client.sent_requests)
        self.assertEqual(
            "send_prepare_timeout",
            runtime.get_last_game_send_block(send_as_id, ".准备超时")["code"],
        )
        close_guard_mock.assert_called_once_with(".准备超时", send_as_id, "send_prepare_timeout")
        self.assertIn("准备超时未发送", audit_mock.await_args.args[0])

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

    async def test_send_timeout_recovers_missing_command_id_from_bot_reply_log(self):
        reply_ts = message_log_recovery.parse_message_log_ts("2026-07-05 15:34:28 UTC+8")
        reply_payload = {
            "ts": "2026-07-05 15:34:28 UTC+8",
            "event_type": "message",
            "message_id": 11489982,
            "chat_id": -1001680975844,
            "sender_id": 8757550896,
            "topic_id": 7310786,
            "reply_to_msg_id": 11489981,
            "text": "你拨动司命盘，为 【炼制】 推下一段命数。\n此推命将在 8 小时 内生效；若你先去做别路之事，便会平添一层逆命劫。",
            "sender_username": "hantianzz_bot",
            "sender_name": "韩天尊",
            "sender_is_bot": True,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2026-07-05.log"
            log_path.write_text(json.dumps(reply_payload, ensure_ascii=False) + "\n", encoding="utf-8")
            with (
                patch.object(message_log_recovery, "MESSAGES_DIR", tmpdir),
                patch.object(runtime, "GAME_SEND_TIMEOUT_RECOVERY_WAIT_SEC", 0.0),
                patch.object(runtime, "recover_sent_command_from_message_log", return_value=None) as command_recover_mock,
                patch.object(runtime.time, "time", return_value=reply_ts + 10),
            ):
                result = await runtime._recover_timed_out_game_send(
                    ".推命 炼制",
                    send_as_id=3765328695,
                    send_started_at=reply_ts - 8,
                    game_group_id=-1001680975844,
                    topic_id=7310786,
                )

        self.assertEqual("reply_to_missing_command", result["event_type"])
        self.assertEqual(11489981, result["message_id"])
        self.assertEqual(11489982, result["reply_message_id"])
        command_recover_mock.assert_called()

    async def test_send_timeout_does_not_steal_reply_when_logged_command_is_other_identity(self):
        reply_ts = message_log_recovery.parse_message_log_ts("2026-07-05 15:34:28 UTC+8")
        entries = [
            {
                "ts": "2026-07-05 15:34:26 UTC+8",
                "event_type": "message",
                "message_id": 11489981,
                "chat_id": -1001680975844,
                "sender_id": 8659059191,
                "topic_id": 0,
                "reply_to_msg_id": 7310786,
                "text": ".推命 炼制",
            },
            {
                "ts": "2026-07-05 15:34:28 UTC+8",
                "event_type": "message",
                "message_id": 11489982,
                "chat_id": -1001680975844,
                "sender_id": 8757550896,
                "topic_id": 7310786,
                "reply_to_msg_id": 11489981,
                "text": "你拨动司命盘，为 【炼制】 推下一段命数。\n此推命将在 8 小时 内生效；若你先去做别路之事，便会平添一层逆命劫。",
            },
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2026-07-05.log"
            log_path.write_text(
                "\n".join(json.dumps(item, ensure_ascii=False) for item in entries) + "\n",
                encoding="utf-8",
            )
            with (
                patch.object(message_log_recovery, "MESSAGES_DIR", tmpdir),
                patch.object(runtime, "GAME_SEND_TIMEOUT_RECOVERY_WAIT_SEC", 0.0),
                patch.object(runtime, "recover_sent_command_from_message_log", return_value=None),
                patch.object(runtime.time, "time", return_value=reply_ts + 10),
            ):
                result = await runtime._recover_timed_out_game_send(
                    ".推命 炼制",
                    send_as_id=3765328695,
                    send_started_at=reply_ts - 8,
                    game_group_id=-1001680975844,
                    topic_id=7310786,
                )

        self.assertIsNone(result)

    async def test_send_queue_timeout_releases_action_guard_placeholder(self):
        send_as_id = 301299112
        runtime._GAME_LAST_SEND_AT = runtime.time.monotonic()
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, 7001)
        client = _FakeClient(["ok"])
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["action_guard_sessions"] = {
                "explore_rift": {
                    "action_key": "explore_rift",
                    "kind": "high_risk",
                    "label": "探寻裂缝",
                    "attempt": 0,
                    "first_sent_at": 0,
                    "last_sent_at": 0,
                    "next_allowed_at": 0,
                    "last_msg_id": 0,
                    "last_command": ".探寻裂缝",
                }
            }

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "get_game_group_id", return_value=123456),
                patch.object(runtime, "get_game_topic_id", return_value=0),
                patch.object(runtime, "get_global_enabled", return_value=True),
                patch.object(runtime, "get_registered_client", return_value=client),
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
                    ".探寻裂缝",
                    send_as_id=send_as_id,
                    priority="normal",
                    track=False,
                    queue_timeout=0.01,
                ),
                timeout=1,
            )

        self.assertIsNone(result)
        self.assertFalse(runtime._GAME_SEND_LOCK.locked())
        self.assertNotIn("explore_rift", state_module.get_identity_state(send_as_id)["action_guard_sessions"])
        self.assertEqual(
            "send_queue_timeout",
            runtime.get_last_game_send_block(send_as_id, ".探寻裂缝")["code"],
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

    async def test_recovery_queue_timeout_covers_commands_already_ahead(self):
        now = 1_700_000_000.0
        state_module.set_global_recovery_throttle_until(now + 900)
        runtime._GAME_SEND_QUEUE_ITEMS.update({
            1: {"status": "sending"},
            2: {"status": "waiting"},
            3: {"status": "waiting"},
        })

        with patch.object(runtime.time, "time", return_value=now):
            timeout = runtime._effective_send_queue_timeout(
                runtime.SEND_PRIORITY_NORMAL,
                command=".搜集军报",
                send_as_id=3504367852,
                intent={"source_module": "慕兰烽烟"},
                queue_timeout=90,
            )

        base_timeout = runtime._minimum_send_queue_timeout_sec(
            runtime.SEND_PRIORITY_NORMAL,
            command=".搜集军报",
            send_as_id=3504367852,
            intent={"source_module": "慕兰烽烟"},
        )
        self.assertGreaterEqual(
            timeout,
            base_timeout + 3 * runtime.GLOBAL_RECOVERY_THROTTLE_SEND_GAP_MAX_SEC,
        )

    async def test_normal_queue_timeout_covers_commands_already_ahead(self):
        state_module.set_global_recovery_throttle_until(0)
        runtime._GAME_SEND_QUEUE_ITEMS.update({
            1: {"status": "sending"},
            2: {"status": "waiting"},
            3: {"status": "waiting"},
            4: {"status": "waiting"},
        })

        timeout = runtime._effective_send_queue_timeout(
            runtime.SEND_PRIORITY_NORMAL,
            command=".公开军报 3",
            send_as_id=301299112,
            intent={"source_module": "慕兰烽烟"},
            queue_timeout=120,
        )

        base_timeout = runtime._minimum_send_queue_timeout_sec(
            runtime.SEND_PRIORITY_NORMAL,
            command=".公开军报 3",
            send_as_id=301299112,
            intent={"source_module": "慕兰烽烟"},
        )
        self.assertGreaterEqual(
            timeout,
            base_timeout + 4 * runtime.NORMAL_SEND_GAP_MAX_SEC,
        )

    def test_classify_game_send_block_distinguishes_unknown_from_unsent(self):
        send_as_id = 301299112
        runtime._record_game_send_block(send_as_id, ".慢返回", "send_timeout", ">60s")
        runtime._record_game_send_block(send_as_id, ".没发出", "send_queue_timeout", ">60s")

        unknown = runtime.classify_game_send_block(send_as_id, ".慢返回")
        unsent = runtime.classify_game_send_block(send_as_id, ".没发出")
        none = runtime.classify_game_send_block(send_as_id, ".不存在")

        self.assertEqual("unknown", unknown["status"])
        self.assertEqual("unsent", unsent["status"])
        self.assertEqual("none", none["status"])
        self.assertTrue(runtime.is_game_send_status_unknown(send_as_id, ".慢返回"))
        self.assertTrue(runtime.is_game_send_definitely_unsent(send_as_id, ".没发出"))

    def test_dynamic_pre_send_guard_code_is_still_definitely_unsent(self):
        send_as_id = 301299112
        runtime._record_game_send_block(
            send_as_id,
            ".探寻裂缝",
            "tianxing_route_pending:探索",
            "天星探索下游动作仍在等待回复",
            definitely_unsent=True,
        )

        block = runtime.classify_game_send_block(send_as_id, ".探寻裂缝")

        self.assertEqual("tianxing_route_pending:探索", block["code"])
        self.assertTrue(block["definitely_unsent"])
        self.assertEqual("unsent", block["status"])

    async def test_global_recovery_hold_blocks_normal_send_as_unsent(self):
        now = 1_700_000_000.0
        send_as_id = 301299112
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_global_enabled(True)
        state_module.set_global_recovery_hold_until(now + 180)
        state_module.set_global_recovery_throttle_until(0)

        with (
            patch.object(runtime.time, "time", return_value=now),
            patch.object(runtime, "send_audit_log", new=AsyncMock()) as audit_mock,
        ):
            result = await runtime.send_game_command(
                ".推命 探索",
                send_as_id=send_as_id,
                priority=runtime.SEND_PRIORITY_REACTIVE,
            )

        self.assertIsNone(result)
        block = runtime.get_last_game_send_block(send_as_id, ".推命 探索", max_age_sec=1_000_000_000)
        self.assertEqual("global_recovery_cooldown", block["code"])
        self.assertEqual(
            "unsent",
            runtime.classify_game_send_block(send_as_id, ".推命 探索", max_age_sec=1_000_000_000)["status"],
        )
        audit_mock.assert_awaited_once()

    def test_global_recovery_throttle_expands_non_probe_send_gap(self):
        now = 1_700_000_000.0
        state_module.set_global_recovery_throttle_until(now + 900)

        with patch.object(runtime.time, "time", return_value=now):
            self.assertEqual(
                (
                    runtime.GLOBAL_RECOVERY_THROTTLE_SEND_GAP_MIN_SEC,
                    runtime.GLOBAL_RECOVERY_THROTTLE_SEND_GAP_MAX_SEC,
                ),
                runtime._get_send_gap_range(runtime.SEND_PRIORITY_RETRY),
            )
            self.assertEqual(
                (
                    runtime.GLOBAL_RECOVERY_THROTTLE_SEND_GAP_MIN_SEC,
                    runtime.GLOBAL_RECOVERY_THROTTLE_SEND_GAP_MAX_SEC,
                ),
                runtime._get_send_gap_range(runtime.SEND_PRIORITY_URGENT_REACTIVE),
            )
            self.assertEqual(
                (runtime.P0_SEND_GAP_MIN_SEC, runtime.P0_SEND_GAP_MAX_SEC),
                runtime._get_send_gap_range(runtime.SEND_PRIORITY_P0),
            )


if __name__ == "__main__":
    unittest.main()
