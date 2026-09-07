import asyncio
import copy
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from model import state as state_module
from model.features import tower
from model.timing import get_day_key


class TowerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved_meta = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        self.identity_id = 990350001
        self.now = 1788748200.0
        self.urls = ["https://t.me/fanrenxiuxian_bot?startapp=df_TEST"]
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (
            ("_TOWER_TASKS", {}), ("_TOWER_RUN_LOCK", asyncio.Lock()),
            ("_TOWER_LAST_RUN_AT", 0.0), ("_TOWER_UPSTREAM_CIRCUIT_UNTIL", 0.0),
            ("_TOWER_PREFERRED_ENTRY_INDEX", 0),
        ):
            self.stack.enter_context(patch.object(tower, name, value))
        self.stack.enter_context(patch.object(tower, "track_background_task", side_effect=lambda task: task))
        self.stack.enter_context(patch.object(tower, "save_state"))
        self.stack.enter_context(patch.object(tower, "console_log"))
        self.stack.enter_context(patch.object(tower, "send_audit_log", new=AsyncMock()))
        self.stack.enter_context(patch.object(tower.time, "time", return_value=self.now))

    async def asyncTearDown(self):
        tasks = list(tower._TOWER_TASKS.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(self.saved_meta)

    def prepare_identity(self):
        tower._TOWER_LAST_RUN_AT = 0.0
        tower._TOWER_UPSTREAM_CIRCUIT_UNTIL = 0.0
        state_module.set_global_enabled(True)
        state_module.set_global_pause_source("")
        state_module.set_channel_send_as_health({})
        if state_module.has_identity(self.identity_id):
            state_module.remove_identity(self.identity_id)
        identity = state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(self.identity_id, username="TowerLifecycle", enabled=True)
        state_module.set_identity_account(self.identity_id, 7101)
        identity.update(tower_enabled=True, next_tower_time=self.now + 1800)
        return identity

    def change_owner(self, change, identity):
        if change == "module_disabled":
            identity["tower_enabled"] = False
        elif change == "identity_disabled":
            state_module.set_identity_enabled(self.identity_id, False)
        elif change in {"removed", "replaced"}:
            state_module.remove_identity(self.identity_id)
            if change == "replaced":
                new = state_module.ensure_identity_registered(self.identity_id)
                state_module.set_identity_account(self.identity_id, 7101)
                new.update(tower_enabled=True, next_tower_time=self.now + 9000)
        elif change == "rebound":
            state_module.set_identity_account(self.identity_id, 7102)
        elif change == "global_paused":
            state_module.set_global_enabled(False)
            state_module.set_global_pause_source("manual")
        elif change == "rescheduled":
            identity["next_tower_time"] = self.now + 9000
        elif change == "completed":
            identity["last_tower_day"] = get_day_key(self.now)
            identity["next_tower_time"] = self.now + 86400

    def launch(self):
        self.assertTrue(tower._launch_tower_worker(self.identity_id, self.urls, scheduled_at=self.now))
        return tower._TOWER_TASKS[self.identity_id]

    async def test_invalidated_queued_worker_never_opens_public_entry(self):
        for boundary in ("created", "lock"):
            for change in (
                "module_disabled", "identity_disabled", "removed", "replaced", "rebound",
                "global_paused", "rescheduled", "completed",
            ):
                with self.subTest(boundary=boundary, change=change):
                    identity = self.prepare_identity()
                    other = state_module.ensure_identity_registered(self.identity_id + 1)
                    lock = tower._tower_run_lock()
                    if boundary == "lock":
                        await lock.acquire()
                    with patch.object(tower, "run_cave_public_tower", new=AsyncMock(return_value={"ok": True})) as runner:
                        task = self.launch()
                        if boundary == "lock":
                            await asyncio.sleep(0)
                            self.assertFalse(task.done())
                        self.change_owner(change, identity)
                        before = copy.deepcopy(state_module._meta_state["identity_states"])
                        if boundary == "lock":
                            lock.release()
                        await task
                        await asyncio.sleep(0)
                    runner.assert_not_awaited()
                    self.assertEqual(before, state_module._meta_state["identity_states"])
                    self.assertIs(other, state_module.get_identity_state(self.identity_id + 1))

    async def test_returning_worker_does_not_write_into_rebound_or_replaced_identity(self):
        for outcome in ("success", "failure", "exception"):
            for change in ("removed", "replaced", "rebound"):
                with self.subTest(outcome=outcome, change=change):
                    identity = self.prepare_identity()
                    state_module.ensure_identity_registered(self.identity_id + 1)
                    started, release = asyncio.Event(), asyncio.Event()

                    async def run(*_args, **_kwargs):
                        started.set()
                        await release.wait()
                        if outcome == "exception":
                            raise RuntimeError("local response failure")
                        return {"ok": outcome == "success", "message": "failed", "extra": {}}

                    with patch.object(tower, "run_cave_public_tower", new=AsyncMock(side_effect=run)) as runner:
                        task = self.launch()
                        try:
                            await asyncio.wait_for(started.wait(), 1)
                            self.change_owner(change, identity)
                            before = copy.deepcopy(state_module._meta_state["identity_states"])
                        finally:
                            release.set()
                        await task
                        await asyncio.sleep(0)
                    runner.assert_awaited_once()
                    self.assertEqual(before, state_module._meta_state["identity_states"])

    async def test_gap_wait_rechecks_before_opening_public_entry(self):
        for change in ("module_disabled", "removed", "replaced", "rebound", "global_paused"):
            with self.subTest(change=change):
                identity = self.prepare_identity()
                tower._TOWER_LAST_RUN_AT = self.now
                real_sleep = asyncio.sleep

                async def invalidate(delay):
                    if delay > 0:
                        self.change_owner(change, identity)
                    await real_sleep(0)

                with patch.object(tower.asyncio, "sleep", new=AsyncMock(side_effect=invalidate)), \
                        patch.object(tower, "run_cave_public_tower", new=AsyncMock()) as runner:
                    await self.launch()
                    await real_sleep(0)
                runner.assert_not_awaited()

    async def test_failure_does_not_fallback_or_rewrite_newer_state(self):
        for outcome in ("failed", "exception"):
            for change in ("module_disabled", "removed", "replaced", "rebound", "rescheduled", "completed"):
                with self.subTest(outcome=outcome, change=change):
                    identity = self.prepare_identity()
                    before = None

                    async def run(*_args, **_kwargs):
                        nonlocal before
                        self.change_owner(change, identity)
                        before = copy.deepcopy(state_module._meta_state["identity_states"])
                        if outcome == "exception":
                            raise RuntimeError("fixture_failure")
                        return {"ok": False, "message": "未开放琉璃问心塔"}

                    with patch.object(tower, "run_cave_public_tower", new=AsyncMock(side_effect=run)) as runner:
                        self.assertTrue(tower._launch_tower_worker(
                            self.identity_id, self.urls + [self.urls[0] + "2"], scheduled_at=self.now,
                        ))
                        await tower._TOWER_TASKS[self.identity_id]
                        await asyncio.sleep(0)
                    runner.assert_awaited_once()
                    self.assertEqual(before, state_module._meta_state["identity_states"])

    async def test_completed_response_keeps_daily_fact_after_switch_off(self):
        for change in ("module_disabled", "identity_disabled", "global_paused"):
            with self.subTest(change=change):
                identity = self.prepare_identity()

                async def run(*_args, **_kwargs):
                    self.change_owner(change, identity)
                    return {"ok": True, "message": "completed", "extra": {}}

                with patch.object(tower, "run_cave_public_tower", new=AsyncMock(side_effect=run)):
                    await self.launch()
                    await asyncio.sleep(0)
                self.assertEqual(get_day_key(self.now), identity["last_tower_day"])

    async def test_channel_freeze_and_maintenance_still_allow_automatic_tower(self):
        identity = self.prepare_identity()
        state_module.set_identity_enabled(self.identity_id, False)
        state_module.set_channel_send_as_health({"status": "closed", "restore_identity_ids": [self.identity_id]})
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("tianzun_maintenance")
        with patch.object(tower, "run_cave_public_tower", new=AsyncMock(return_value={"ok": True})) as runner:
            await self.launch()
        runner.assert_awaited_once()
        self.assertEqual(get_day_key(self.now), identity["last_tower_day"])

    async def test_active_task_does_not_extend_its_lease_on_another_scheduler_tick(self):
        identity = self.prepare_identity()
        started, release = asyncio.Event(), asyncio.Event()

        async def run(*_args, **_kwargs):
            started.set()
            await release.wait()
            return {"ok": True}

        with patch.object(tower, "run_cave_public_tower", new=AsyncMock(side_effect=run)) as runner:
            task = self.launch()
            try:
                await asyncio.wait_for(started.wait(), 1)
                before = copy.deepcopy(identity)
                with state_module.use_identity(self.identity_id):
                    await tower.run_tower_scheduler(self.now + 3600)
                self.assertEqual(before, identity)
            finally:
                release.set()
                await task
        runner.assert_awaited_once()

    def test_status_without_own_record_does_not_show_another_identity_result(self):
        self.prepare_identity()
        with state_module.use_identity(self.identity_id), patch.object(
            tower, "get_miniapp_state_snapshot", return_value={"rows": [{
                "identity_id": self.identity_id + 1,
                "state": {"phase": "completed", "gains": {"修为": 999999}},
            }]},
        ):
            self.assertEqual({}, tower._latest_tower_record())

    def test_deleted_explicit_context_does_not_select_another_identity_record(self):
        self.prepare_identity()
        state_module.ensure_identity_registered(self.identity_id + 1)
        with state_module.use_identity(self.identity_id), patch.object(
            tower, "get_miniapp_state_snapshot", return_value={"rows": [{
                "identity_id": self.identity_id + 1, "state": {"phase": "completed"},
            }]},
        ):
            state_module.remove_identity(self.identity_id)
            self.assertEqual({}, tower._latest_tower_record())
