import asyncio
import copy
import threading
import time
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, Mock, patch

from model import state as state_module
from model.features import cave_treasure_runtime as cave
from model.features import miniapp_common, tower, tower_miniapp
from model.timing import get_day_key


class TowerThreadLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved_meta = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        self.identity_id = 990360001
        self.identity = state_module.ensure_identity_registered(self.identity_id)
        self.identity["tower_enabled"] = True
        state_module.set_identity_account(self.identity_id, 7301)
        self.url = "https://t.me/fanrenxiuxian_bot?startapp=df_TEST"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (
            ("_TOWER_TASKS", {}), ("_TOWER_RUN_LOCK", asyncio.Lock()),
            ("_TOWER_LAST_RUN_AT", 0.0), ("_TOWER_UPSTREAM_CIRCUIT_UNTIL", 0.0),
        ):
            self.stack.enter_context(patch.object(tower, name, value))
        self.stack.enter_context(patch.object(tower, "track_background_task", side_effect=lambda task: task))
        self.stack.enter_context(patch.object(tower, "save_state"))
        self.stack.enter_context(patch.object(tower, "console_log"))
        self.stack.enter_context(patch.object(cave, "_PUBLIC_ENTRY_LOCKS", {}))
        self.stack.enter_context(patch.object(cave, "_tower_capture_store", return_value=None))
        self.stack.enter_context(patch.object(cave, "_capture_store", return_value=None))
        self.record = self.stack.enter_context(patch.object(cave, "record_miniapp_state"))
        self.audit = self.stack.enter_context(patch.object(cave, "send_audit_log", new=AsyncMock()))
        self.real_session_loader = cave._load_cave_public_identity_session
        self.real_external_flow = cave.run_cave_external_action_production_flow
        self.stack.enter_context(patch.object(cave, "_load_cave_public_identity_session", new=AsyncMock(return_value={
            "ok": True, "init_data": "fixture_init_data", "player_id": self.identity_id,
            "result": {"data": {"raw": {"account": {"externalApps": {
                "groups": [{"apps": [{"key": "pagoda", "action": "pagoda", "available": True}]}],
            }}}}},
        })))
        self.stack.enter_context(patch.object(cave, "run_cave_external_action_production_flow", new=AsyncMock(return_value={
            "ok": True, "data": {"url": "/miniapp/xianxia-pagoda?startapp=pagoda_TEST"},
        })))
        self.pool = miniapp_common._MiniAppSessionPool()
        self.stack.enter_context(patch.object(miniapp_common, "_MINIAPP_SESSION_POOL", self.pool))
        self.requests = []
        self.started, self.finished = asyncio.Event(), asyncio.Event()
        self.release = threading.Event()
        self.session = Mock()
        self.session.request.side_effect = self.transport
        self.stack.enter_context(patch.object(miniapp_common.requests, "Session", return_value=self.session))
        self.real_flow = tower_miniapp.run_tower_miniapp_lab_flow
        self.stack.enter_context(patch.object(tower_miniapp, "run_tower_miniapp_lab_flow", side_effect=self.thread_flow))

    async def asyncSetUp(self):
        self.loop = asyncio.get_running_loop()

    async def asyncTearDown(self):
        self.release.set()
        if self.started.is_set():
            await asyncio.wait_for(self.finished.wait(), 3)
        self.pool.close()

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(self.saved_meta)

    def thread_flow(self, **kwargs):
        try:
            return self.real_flow(**kwargs)
        finally:
            self.loop.call_soon_threadsafe(self.finished.set)

    def transport(self, _method, url, **_kwargs):
        step = url.rsplit("/", 1)[-1]
        if "/xianxia-dwelling/" in url:
            step = "dwelling_" + step
        self.requests.append(step)
        if step == self.block_step:
            self.loop.call_soon_threadsafe(self.started.set)
            if not self.release.wait(3):
                raise TimeoutError("fixture request was not released")
            if step.startswith("dwelling_"):
                self.loop.call_soon_threadsafe(self.finished.set)
        if step == "dwelling_start":
            return 200, {"ok": True, "account": {"playerId": self.identity_id, "deferredPending": True}}
        if step == "dwelling_details":
            return 200, {"ok": True, "account": {"playerId": self.identity_id, "externalApps": {
                "groups": [{"apps": [{"key": "pagoda", "action": "pagoda", "available": True}]}],
            }}}
        if step == "dwelling_external":
            return 200, {"ok": True, "url": "/miniapp/xianxia-pagoda?startapp=pagoda_TEST"}
        if step == "start":
            return 200, {"ok": True, "state": {"canChallenge": True}}
        return 200, {"ok": True, "state": {"canChallenge": False, "todayHighest": 9}, "replay": {"clearedCount": 9}}

    def launch_public(self):
        return asyncio.create_task(cave.run_cave_public_tower(
            self.identity_id, self.url,
            operation_check=lambda: bool(self.identity.get("tower_enabled")),
        ))

    async def test_cancelled_start_joins_thread_and_never_challenges(self):
        self.block_step = "start"
        task = self.launch_public()
        try:
            await asyncio.wait_for(self.started.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            pending_while_http_runs = not task.done()
            lock_held_while_http_runs = cave.is_cave_public_entry_busy(self.identity_id)
            task.cancel()
            await asyncio.sleep(0)
        finally:
            self.release.set()
            outcome = (await asyncio.gather(task, return_exceptions=True))[0]
        await asyncio.wait_for(self.finished.wait(), 2)
        self.assertTrue(pending_while_http_runs)
        self.assertTrue(lock_held_while_http_runs)
        self.assertIsInstance(outcome, asyncio.CancelledError)
        self.assertEqual(["start"], self.requests)
        self.record.assert_not_called()

    async def test_module_switch_off_during_start_prevents_challenge(self):
        self.block_step = "start"
        task = self.launch_public()
        try:
            await asyncio.wait_for(self.started.wait(), 2)
            self.identity["tower_enabled"] = False
        finally:
            self.release.set()
            outcome = await task
        self.assertFalse(outcome["ok"])
        self.assertEqual(["start"], self.requests)
        self.record.assert_not_called()

    async def test_cancellation_keeps_confirmed_challenge_fact_before_releasing_locks(self):
        self.block_step = "challenge"
        self.assertTrue(tower._launch_tower_worker(self.identity_id, [self.url], scheduled_at=time.time()))
        task = tower._TOWER_TASKS[self.identity_id]
        try:
            await asyncio.wait_for(self.started.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            locks_held = cave.is_cave_public_entry_busy(self.identity_id) and tower._tower_run_lock().locked()
        finally:
            self.release.set()
            outcome = (await asyncio.gather(task, return_exceptions=True))[0]
        await asyncio.wait_for(self.finished.wait(), 2)
        self.assertTrue(locks_held)
        self.assertIsInstance(outcome, asyncio.CancelledError)
        self.assertEqual(["start", "challenge"], self.requests)
        self.assertEqual(get_day_key(), self.identity["last_tower_day"])
        self.record.assert_called_once()

    async def test_public_handshake_cancellation_keeps_lock_until_http_returns(self):
        for step in ("dwelling_start", "dwelling_details", "dwelling_external"):
            with self.subTest(step=step):
                self.block_step = step
                self.requests.clear()
                self.record.reset_mock()
                self.started.clear()
                self.finished.clear()
                self.release.clear()
                with patch.object(cave, "_load_cave_public_identity_session", self.real_session_loader), \
                        patch.object(cave, "run_cave_external_action_production_flow", self.real_external_flow), \
                        patch.object(cave, "request_cave_treasure_miniapp_init_data", new=AsyncMock(return_value="fixture_init_data")):
                    task = self.launch_public()
                    try:
                        await asyncio.wait_for(self.started.wait(), 2)
                        task.cancel()
                        await asyncio.sleep(0)
                        await asyncio.sleep(0)
                        held = cave.is_cave_public_entry_busy(self.identity_id) and not task.done()
                    finally:
                        self.release.set()
                        outcome = (await asyncio.gather(task, return_exceptions=True))[0]
                self.assertTrue(held)
                self.assertIsInstance(outcome, asyncio.CancelledError)
                sequence = ["dwelling_start", "dwelling_details", "dwelling_external"]
                self.assertEqual(sequence[:sequence.index(step) + 1], self.requests)
                self.assertFalse(any(call.args[1] == "tower" for call in self.record.call_args_list))

    async def test_account_rebind_during_start_prevents_challenge(self):
        self.block_step = "start"
        task = self.launch_public()
        try:
            await asyncio.wait_for(self.started.wait(), 2)
            state_module.set_identity_account(self.identity_id, 7302)
        finally:
            self.release.set()
            outcome = await task
        self.assertFalse(outcome["ok"])
        self.assertEqual(["start"], self.requests)
        self.record.assert_not_called()

    async def test_complete_public_chain_reaches_challenge_and_saves_daily_fact(self):
        self.block_step = "none"
        with patch.object(cave, "_load_cave_public_identity_session", self.real_session_loader), \
                patch.object(cave, "run_cave_external_action_production_flow", self.real_external_flow), \
                patch.object(cave, "request_cave_treasure_miniapp_init_data", new=AsyncMock(return_value="fixture_init_data")):
            self.assertTrue(tower._launch_tower_worker(self.identity_id, [self.url], scheduled_at=time.time()))
            await tower._TOWER_TASKS[self.identity_id]
        self.assertEqual(["dwelling_start", "dwelling_details", "dwelling_external", "start", "challenge"], self.requests)
        self.assertEqual(get_day_key(), self.identity["last_tower_day"])
        self.assertEqual(0, self.identity["tower_retry_count"])
        records = [call for call in self.record.call_args_list if call.args[1] == "tower"]
        self.assertEqual(1, len(records))
        self.assertEqual("completed", records[0].args[2]["phase"])
        self.assertEqual(9, records[0].args[2]["cleared_count"])
        for call in self.session.request.call_args_list:
            self.assertEqual("fixture_init_data", call.kwargs["json"]["initData"])
            if call.args[1].endswith("/details"):
                self.assertEqual(self.identity_id, call.kwargs["json"]["playerId"])
            elif call.args[1].endswith("/external"):
                self.assertEqual(str(self.identity_id), call.kwargs["json"]["playerId"])

    async def test_notification_cancellation_keeps_confirmed_daily_fact(self):
        self.block_step = "none"
        notified = asyncio.Event()

        async def notify(*_args, **_kwargs):
            notified.set()
            await asyncio.Event().wait()

        self.audit.side_effect = notify
        self.assertTrue(tower._launch_tower_worker(self.identity_id, [self.url], scheduled_at=time.time()))
        task = tower._TOWER_TASKS[self.identity_id]
        try:
            await asyncio.wait_for(notified.wait(), 2)
        finally:
            task.cancel()
            outcome = (await asyncio.gather(task, return_exceptions=True))[0]
        self.assertIsInstance(outcome, asyncio.CancelledError)
        self.assertEqual(get_day_key(), self.identity["last_tower_day"])
        self.record.assert_called_once()

    async def test_notification_exception_cannot_turn_completion_into_retry(self):
        self.block_step = "none"
        self.audit.side_effect = RuntimeError("fixture notification failed")
        self.assertTrue(tower._launch_tower_worker(self.identity_id, [self.url], scheduled_at=time.time()))
        await tower._TOWER_TASKS[self.identity_id]
        self.assertEqual(get_day_key(), self.identity["last_tower_day"])
        self.assertEqual(0, self.identity["tower_retry_count"])
        self.record.assert_called_once()
