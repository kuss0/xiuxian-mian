import asyncio
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from model import app, runtime, ui
from model.features import quiz_ai


class AppShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tasks = []
        self.events = []
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.old_quiesced = runtime.is_game_send_quiesced()
        self.addCleanup(runtime.set_game_send_quiesced, self.old_quiesced)
        self.stack.enter_context(patch.object(runtime, "_background_tasks", set()))
        self.stack.enter_context(patch.object(runtime, "_GAME_SEND_TASKS", {}))
        for name in ("_identity_scheduler_task", "_phaseful_scheduler_task", "_small_world_scheduler_task", "_log_bot_callback_task"):
            self.stack.enter_context(patch.object(app, name, None))
        self.stack.enter_context(patch.object(app, "stop_ui_server", new=AsyncMock(side_effect=lambda: self.events.append("ui_closed"))))
        self.client = SimpleNamespace(disconnect=AsyncMock(side_effect=lambda: self.events.append("disconnected")))
        self.stack.enter_context(patch.object(app, "client", self.client))
        self.stack.enter_context(patch.object(app, "get_all_clients", return_value={1: self.client}))
        self.save = self.stack.enter_context(patch.object(app, "save_state", side_effect=lambda: self.events.append("saved") or True))

    async def asyncTearDown(self):
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    async def _worker(self, name, *, fail=False):
        async def run():
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                self.events.append(name)
                if fail:
                    raise RuntimeError("injected cleanup failure")

        task = asyncio.create_task(run())
        self.tasks.append(task)
        await asyncio.sleep(0)
        return task

    async def test_shutdown_joins_identity_and_background_before_final_save(self):
        app._identity_scheduler_task = await self._worker("identity_joined")
        background = await self._worker("background_joined")
        runtime._background_tasks.add(background)

        await app.shutdown()

        self.assertTrue(runtime.is_game_send_quiesced())
        self.assertLess(self.events.index("identity_joined"), self.events.index("saved"))
        self.assertLess(self.events.index("background_joined"), self.events.index("saved"))
        self.assertLess(self.events.index("disconnected"), self.events.index("saved"))
        self.assertEqual("saved", self.events[-1])
        self.client.disconnect.assert_awaited_once()

    async def test_one_scheduler_cleanup_failure_does_not_abandon_other_cleanup(self):
        app._phaseful_scheduler_task = await self._worker("phaseful_joined", fail=True)
        app._small_world_scheduler_task = await self._worker("small_world_joined")

        await app.shutdown()

        self.assertIn("small_world_joined", self.events)
        self.assertIn("disconnected", self.events)
        self.save.assert_called_once()

    async def test_repeated_quiesce_does_not_cancel_a_running_finalizer_again(self):
        task = await self._worker("identity_joined")
        app._identity_scheduler_task = task
        app._cancel_identity_schedulers()
        app._cancel_identity_schedulers()
        self.assertEqual(1, task.cancelling())

    async def test_stop_ui_waits_for_active_request_cleanup(self):
        started = asyncio.Event()

        async def read_request(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                self.events.append("request_cleaned")

        writer = SimpleNamespace(
            get_extra_info=Mock(return_value=None), drain=AsyncMock(),
            close=Mock(), wait_closed=AsyncMock(), write=Mock(),
        )
        with (
            patch.object(ui, "_ui_server", None),
            patch.object(ui, "_ui_request_tasks", set(), create=True),
            patch.object(ui, "_ui_stopping", False, create=True),
            patch.object(ui, "_pending_login", {}),
            patch.object(ui, "_read_ui_request", side_effect=read_request),
        ):
            request = asyncio.create_task(ui.handle_ui_http(None, writer))
            self.tasks.append(request)
            await asyncio.wait_for(started.wait(), 1)
            await ui.stop_ui_server()
            self.assertTrue(request.done())
            self.assertIn("request_cleaned", self.events)
            writer.close.assert_called_once()

    async def test_pending_login_cleanup_joins_worker_before_disconnect(self):
        worker = await self._worker("login_joined")
        with (
            patch.object(ui, "_pending_login", {"test": {"wait_task": worker, "client": self.client}}),
            patch.object(ui, "_disconnect_pending_login_client", new=AsyncMock(side_effect=lambda _client: self.events.append("login_disconnected") or True)),
        ):
            await ui._clear_pending_login("test")
            self.assertTrue(worker.done())
        self.assertLess(self.events.index("login_joined"), self.events.index("login_disconnected"))

    async def test_quiz_cancellation_joins_provider_tasks(self):
        started = asyncio.Event()
        finished = asyncio.Event()

        async def provider(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0)
                finished.set()

        with (
            patch.object(quiz_ai, "_enabled_providers", return_value=[{"id": "test"}]),
            patch.object(quiz_ai, "_call_provider", side_effect=provider),
        ):
            task = asyncio.create_task(quiz_ai.suggest_quiz_answer_multi("test", [], {}))
            self.tasks.append(task)
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(finished.is_set())

    async def test_quiesce_rejects_new_background_actions(self):
        action = AsyncMock()
        runtime.set_game_send_quiesced(True)
        task = runtime._fire_and_forget(action())
        self.tasks.append(task)
        await asyncio.gather(task, return_exceptions=True)
        action.assert_not_awaited()
        self.assertTrue(task.cancelled())

    async def test_failed_drain_skips_final_save(self):
        with patch.object(app, "cancel_and_join_tasks", new=AsyncMock(return_value=False)):
            self.assertFalse(await app.shutdown())
        self.save.assert_not_called()
        self.client.disconnect.assert_awaited_once()
