import asyncio
import copy
import sys
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from model import app
from model import state as state_module


SCANS = {
    "wild_training": ("_run_due_wild_training_retry_schedulers", "run_wild_training_scheduler"),
    "explore_rift": ("_run_due_explore_rift_schedulers", "run_explore_rift_scheduler"),
    "concubine": ("_run_due_concubine_schedulers", "run_concubine_scheduler"),
    "tianxing": ("_run_due_tianxing_schedulers", "run_tianxing_scheduler"),
    "followup": ("_run_tianxing_timeline_followup_identity_schedulers", "run_tianxing_timeline_followup_scheduler"),
}
OWNER_CHANGES = ("removed", "replaced", "rebound", "disabled", "module_off", "offline", "weak", "global_paused")
TIMED_SCANS = tuple(kind for kind in SCANS if kind != "followup")


class DueSchedulerOwnershipTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original = copy.deepcopy(state_module._meta_state)
        self.identities = [998811, 998812, 998813]
        self.now = 1_700_000_000.0
        self.clock = self.now
        self.offline = set()
        self.weak = set()

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(self.original)

    @contextmanager
    def case(self, kind):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self.original))
        state_module.state["global_enabled"] = True
        self.clock = self.now
        self.offline.clear()
        self.weak.clear()
        for index, identity_id in enumerate(self.identities):
            state_module.remove_identity(identity_id)
            bucket = state_module.ensure_identity_registered(identity_id)
            state_module.set_identity_account(identity_id, 7100 + index)
            state_module.update_send_as_profile(identity_id, enabled=True)
            module = "tianxing" if kind == "followup" else kind
            bucket[f"{module}_enabled"] = True
            bucket[f"next_{module}_time"] = self.now - 300 + index * 100
            if module == "tianxing":
                bucket["tianxing_observation"] = {"auto_next_time": self.now - 300 + index * 100}

        with ExitStack() as stack:
            patches = {
                "get_identity_ids": dict(return_value=list(self.identities)),
                "_is_identity_account_offline": dict(side_effect=lambda identity_id: identity_id in self.offline),
                "is_identity_weak": dict(side_effect=lambda identity_id, _now: identity_id in self.weak),
                "has_phaseful_summary_block": dict(return_value=False),
                "_has_tianxing_phaseful_summary_block": dict(return_value=False),
                "has_tianxing_timeline_due_work": dict(return_value=True),
                "_tianxing_downstream_prepare_windows": dict(return_value=[]),
                "_tianxing_fast_due_info": dict(side_effect=lambda _now: {
                    "due_at": state_module.state.get("tianxing_observation", {}).get("auto_next_time", 0),
                    "priority": 1,
                    "tianji": 1,
                }),
                "reconcile_wild_training_daily_reset_spread": dict(),
                "console_log": dict(),
                "mark_dirty": dict(),
            }
            for name, kwargs in patches.items():
                stack.enter_context(patch.object(app, name, **kwargs))
            stack.enter_context(patch.object(app.time, "time", side_effect=lambda: self.clock))
            yield getattr(app, SCANS[kind][0]), SCANS[kind][1]

    def change_owner(self, kind, identity_id, change):
        module = "tianxing" if kind == "followup" else kind
        if change in {"removed", "replaced"}:
            state_module.remove_identity(identity_id)
            if change == "replaced":
                bucket = state_module.ensure_identity_registered(identity_id)
                state_module.set_identity_account(identity_id, 7100 + self.identities.index(identity_id))
                bucket[f"{module}_enabled"] = True
                bucket[f"next_{module}_time"] = self.now + 7200
                bucket["tianxing_observation"] = {"auto_next_time": self.now + 7200}
        elif change == "rebound":
            state_module.set_identity_account(identity_id, 7200)
        elif change == "disabled":
            state_module.update_send_as_profile(identity_id, enabled=False)
        elif change == "module_off":
            state_module.get_identity_state(identity_id)[f"{module}_enabled"] = False
        elif change == "offline":
            self.offline.add(identity_id)
        elif change == "weak":
            self.weak.add(identity_id)
        elif change == "global_paused":
            state_module.state["global_enabled"] = False
        elif change == "new_result":
            bucket = state_module.get_identity_state(identity_id)
            bucket[f"next_{module}_time"] = self.now + 12 * 3600
            bucket["tianxing_observation"] = {"auto_next_time": self.now + 12 * 3600}
        else:
            self.fail(f"unrecognized change: {change}")
        return copy.deepcopy(state_module.get_identity_state(identity_id)) if state_module.has_identity(identity_id) else None

    def assert_owner_unchanged(self, identity_id, expected):
        if expected is None:
            self.assertFalse(state_module.has_identity(identity_id))
            self.assertNotIn(identity_id, state_module._meta_state["identity_states"])
        else:
            self.assertEqual(state_module.get_identity_state(identity_id), expected)

    async def test_queued_candidate_is_not_run_after_owner_changes(self):
        first_id, changed_id, final_id = self.identities
        for kind in SCANS:
            for change in OWNER_CHANGES:
                with self.subTest(kind=kind, change=change), self.case(kind) as (scan, scheduler_name):
                    seen = []
                    expected = None

                    async def scheduler(_now):
                        nonlocal expected
                        current = state_module.get_current_identity_id()
                        seen.append(current)
                        await asyncio.sleep(0)
                        if current == first_id:
                            expected = self.change_owner(kind, changed_id, change)
                        return {"active": True}

                    with patch.object(app, scheduler_name, new=AsyncMock(side_effect=scheduler)):
                        await scan(self.now, limit=3)
                    self.assertEqual(seen, [first_id] if change == "global_paused" else [first_id, final_id])
                    self.assert_owner_unchanged(changed_id, expected)

    async def test_candidate_dispatch_rechecks_the_original_owner(self):
        changed_id, second_id, final_id = self.identities
        real_wait_for = asyncio.wait_for
        for kind in TIMED_SCANS:
            for change in OWNER_CHANGES:
                with self.subTest(kind=kind, change=change), self.case(kind) as (scan, scheduler_name):
                    seen = []
                    expected = None
                    changed = False

                    async def wait_for(awaitable, *, timeout):
                        nonlocal expected, changed
                        if not changed:
                            changed = True
                            expected = self.change_owner(kind, changed_id, change)
                        return await real_wait_for(awaitable, timeout=timeout)

                    async def scheduler(_now):
                        seen.append(state_module.get_current_identity_id())

                    with (
                        patch.object(app, scheduler_name, new=AsyncMock(side_effect=scheduler)),
                        patch.object(app.asyncio, "wait_for", side_effect=wait_for),
                    ):
                        await scan(self.now, limit=3)
                    self.assertEqual(seen, [] if change == "global_paused" else [second_id, final_id])
                    self.assert_owner_unchanged(changed_id, expected)

    async def test_late_failure_does_not_overwrite_a_changed_owner_or_new_result(self):
        changed_id, second_id, final_id = self.identities
        for kind in TIMED_SCANS:
            for change in OWNER_CHANGES + ("new_result",):
                for error_type in (RuntimeError, asyncio.TimeoutError):
                    with self.subTest(kind=kind, change=change, error=error_type.__name__), self.case(kind) as (scan, scheduler_name):
                        seen = []
                        expected = None

                        async def scheduler(_now):
                            nonlocal expected
                            current = state_module.get_current_identity_id()
                            seen.append(current)
                            await asyncio.sleep(0)
                            if current == changed_id:
                                expected = self.change_owner(kind, changed_id, change)
                                raise error_type("interleaved scheduler failure")

                        with patch.object(app, scheduler_name, new=AsyncMock(side_effect=scheduler)):
                            await scan(self.now, limit=3)
                        self.assertEqual(seen, [changed_id] if change == "global_paused" else [changed_id, second_id, final_id])
                        self.assert_owner_unchanged(changed_id, expected)

    async def test_tianxing_timeline_result_cannot_release_an_invalidated_role(self):
        changed_id, second_id, final_id = self.identities
        for change in OWNER_CHANGES:
            with self.subTest(change=change), self.case("tianxing") as (scan, scheduler_name):
                seen = []
                expected = None

                async def timeline(_now, *, windows):
                    nonlocal expected
                    await asyncio.sleep(0)
                    if state_module.get_current_identity_id() == changed_id:
                        expected = self.change_owner("tianxing", changed_id, change)

                async def scheduler(_now):
                    seen.append(state_module.get_current_identity_id())

                with (
                    patch.object(app, "_tianxing_downstream_prepare_windows", return_value=[{"route": "explore"}]),
                    patch.object(app, "run_tianxing_timeline_scheduler", new=AsyncMock(side_effect=timeline)),
                    patch.object(app, scheduler_name, new=AsyncMock(side_effect=scheduler)),
                ):
                    await scan(self.now, limit=3)
                self.assertEqual(seen, [] if change == "global_paused" else [second_id, final_id])
                self.assert_owner_unchanged(changed_id, expected)

    async def test_tianxing_refreshes_time_after_awaited_preparation(self):
        with self.case("tianxing") as (scan, scheduler_name):
            async def timeline(_now, *, windows):
                await asyncio.sleep(0)
                self.clock += 45

            with (
                patch.object(app, "_tianxing_downstream_prepare_windows", return_value=[{"route": "explore"}]),
                patch.object(app, "run_tianxing_timeline_scheduler", new=AsyncMock(side_effect=timeline)),
                patch.object(app, scheduler_name, new=AsyncMock()) as scheduler,
            ):
                await scan(self.now, limit=1)
            scheduler.assert_awaited_once_with(self.now + 45)

    async def test_unchanged_failed_candidate_retains_bounded_backoff(self):
        first_id = self.identities[0]
        for kind in TIMED_SCANS:
            for error_type in (RuntimeError, asyncio.TimeoutError):
                with self.subTest(kind=kind, error=error_type.__name__), self.case(kind) as (scan, scheduler_name):
                    seen = []

                    async def scheduler(_now):
                        current = state_module.get_current_identity_id()
                        seen.append(current)
                        await asyncio.sleep(0)
                        if current == first_id:
                            raise error_type("current scheduler failure")

                    with patch.object(app, scheduler_name, new=AsyncMock(side_effect=scheduler)):
                        await scan(self.now, limit=3)
                    self.assertEqual(seen, self.identities)
                    bucket = state_module.get_identity_state(first_id)
                    if kind == "tianxing":
                        delay = 60 if error_type is asyncio.TimeoutError else 120
                        self.assertEqual(bucket["tianxing_observation"]["auto_next_time"], self.now + delay)
                    else:
                        self.assertEqual(bucket[f"next_{kind}_time"], self.now + 120)

    async def test_caller_cancellation_drains_candidate_without_business_backoff(self):
        first_id = self.identities[0]
        for kind in TIMED_SCANS:
            with self.subTest(kind=kind), self.case(kind) as (scan, scheduler_name):
                started = asyncio.Event()
                drained = asyncio.Event()
                expected = copy.deepcopy(state_module.get_identity_state(first_id))
                seen = []

                async def scheduler(_now):
                    seen.append(state_module.get_current_identity_id())
                    started.set()
                    try:
                        await asyncio.Event().wait()
                    finally:
                        drained.set()

                with patch.object(app, scheduler_name, new=AsyncMock(side_effect=scheduler)):
                    task = asyncio.create_task(scan(self.now, limit=3))
                    await asyncio.wait_for(started.wait(), timeout=1)
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertTrue(drained.is_set())
                self.assertEqual(seen, [first_id])
                self.assert_owner_unchanged(first_id, expected)
