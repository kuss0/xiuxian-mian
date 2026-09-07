import asyncio
import copy
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from model import state as state_module
from model.features import cave_treasure_runtime as cave


class CaveTowerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.saved_meta = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        self.identity_id = 990350011
        self.now = 1788748200.0
        self.url = "https://t.me/fanrenxiuxian_bot?startapp=df_TEST"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(cave, "_PUBLIC_ENTRY_LOCKS", {}))
        self.record = self.stack.enter_context(patch.object(cave, "record_miniapp_state"))
        self.audit = self.stack.enter_context(patch.object(cave, "send_audit_log", new=AsyncMock()))
        self.stack.enter_context(patch.object(cave, "_capture_store", return_value=None))
        self.stack.enter_context(patch.object(cave, "_tower_capture_store", return_value=None))
        self.prepare_identity()
        self.session = {
            "ok": True,
            "init_data": "fixture_init_data",
            "player_id": self.identity_id,
            "result": {"ok": True, "data": {"raw": {"account": {"externalApps": {
                "groups": [{"apps": [{"key": "pagoda", "action": "pagoda", "available": True}]}],
            }}}}},
        }
        self.external = {"ok": True, "data": {"url": "/miniapp/xianxia-pagoda?startapp=pagoda_TEST"}}
        self.challenge = {"ok": True, "status": "challenged", "data": {"challenged": True}}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(self.saved_meta)

    def prepare_identity(self):
        if state_module.has_identity(self.identity_id):
            state_module.remove_identity(self.identity_id)
        self.identity = state_module.ensure_identity_registered(self.identity_id)
        self.identity["tower_enabled"] = True
        state_module.set_identity_account(self.identity_id, 7201)
        state_module.set_identity_enabled(self.identity_id, True)
        state_module.set_global_enabled(True)
        state_module.set_global_pause_source("")
        state_module.set_channel_send_as_health({})
        self.record.reset_mock()
        self.audit.reset_mock()

    def change(self, change):
        if change in {"removed", "replaced"}:
            state_module.remove_identity(self.identity_id)
            if change == "replaced":
                state_module.ensure_identity_registered(self.identity_id)
                state_module.set_identity_account(self.identity_id, 7201)
        elif change == "rebound":
            state_module.set_identity_account(self.identity_id, 7202)
        elif change == "module_disabled":
            self.identity["tower_enabled"] = False
        elif change == "identity_disabled":
            state_module.set_identity_enabled(self.identity_id, False)
        elif change == "global_paused":
            state_module.set_global_enabled(False)
            state_module.set_global_pause_source("manual")

    async def run_public_with_change(self, boundary, change, *, successful=True, automatic=False):
        payloads = {"session": self.session, "external": self.external, "challenge": self.challenge}

        def side_effect(step):
            async def complete(*_args, **_kwargs):
                await asyncio.sleep(0)
                if boundary == step:
                    self.change(change)
                    if not successful:
                        return {"ok": False, "error": "fixture_failure"}
                return copy.deepcopy(payloads[step])
            return complete

        with patch.object(cave, "_load_cave_public_identity_session", new=AsyncMock(side_effect=side_effect("session"))) as session, \
                patch.object(cave, "run_cave_external_action_production_flow", new=AsyncMock(side_effect=side_effect("external"))) as external, \
                patch.object(cave, "run_tower_miniapp_production_flow", new=AsyncMock(side_effect=side_effect("challenge"))) as challenge:
            options = {"operation_check": lambda: bool(self.identity.get("tower_enabled"))} if automatic else {}
            result = await cave.run_cave_public_tower(self.identity_id, self.url, now=self.now, **options)
        return result, (session, external, challenge)

    async def test_old_account_and_replaced_identity_results_are_discarded(self):
        for boundary in ("session", "external", "challenge"):
            for change in ("removed", "replaced", "rebound"):
                for successful in (False, True):
                    with self.subTest(boundary=boundary, change=change, successful=successful):
                        self.prepare_identity()
                        result, calls = await self.run_public_with_change(boundary, change, successful=successful)
                        self.assertFalse(result["ok"])
                        completed = ("session", "external", "challenge").index(boundary) + 1
                        self.assertEqual([1] * completed + [0] * (3 - completed), [call.await_count for call in calls])
                        self.record.assert_not_called()
                        self.audit.assert_not_awaited()

    async def test_disable_and_manual_pause_stop_before_challenge(self):
        for boundary in ("session", "external"):
            for change in ("module_disabled", "identity_disabled", "global_paused"):
                with self.subTest(boundary=boundary, change=change):
                    self.prepare_identity()
                    result, calls = await self.run_public_with_change(boundary, change, automatic=True)
                    self.assertFalse(result["ok"])
                    calls[2].assert_not_awaited()
                    if boundary == "session":
                        calls[1].assert_not_awaited()
                    self.record.assert_not_called()
                    self.audit.assert_not_awaited()

    async def test_confirmed_result_survives_switch_off_but_failed_result_does_not(self):
        for change in ("module_disabled", "identity_disabled", "global_paused"):
            for successful in (False, True):
                with self.subTest(change=change, successful=successful):
                    self.prepare_identity()
                    result, _calls = await self.run_public_with_change("challenge", change, successful=successful, automatic=True)
                    self.assertEqual(successful, result["ok"])
                    self.assertEqual(int(successful), self.record.call_count)
                    self.assertEqual(int(successful), self.audit.await_count)

    async def test_manual_call_does_not_depend_on_automatic_switch(self):
        self.identity["tower_enabled"] = False
        result, calls = await self.run_public_with_change("", "")
        self.assertTrue(result["ok"])
        calls[2].assert_awaited_once()

    async def test_channel_send_freeze_and_maintenance_do_not_disable_public_entry(self):
        state_module.set_global_enabled(False)
        state_module.set_global_pause_source("tianzun_maintenance")
        state_module.set_identity_enabled(self.identity_id, False)
        state_module.set_channel_send_as_health({"status": "closed", "restore_identity_ids": [self.identity_id]})
        result, calls = await self.run_public_with_change("", "", automatic=True)
        self.assertTrue(result["ok"])
        calls[2].assert_awaited_once()

    async def test_session_loading_rechecks_owner_after_each_await(self):
        for boundary in ("init", "initial", "selected", "details"):
            for change in ("removed", "replaced", "rebound"):
                with self.subTest(boundary=boundary, change=change):
                    self.prepare_identity()
                    steps = []

                    async def result_for(step, payload):
                        steps.append(step)
                        await asyncio.sleep(0)
                        if step == boundary:
                            self.change(change)
                        return payload

                    async def init(*_args, **_kwargs):
                        return await result_for("init", "fixture_init_data")

                    async def start(*_args, **kwargs):
                        selected = bool(kwargs.get("player_id"))
                        return await result_for("selected" if selected else "initial", {
                            "ok": True,
                            "data": {"overview": {"player_id": self.identity_id if selected else 99}, "raw": {}},
                        })

                    async def details(*_args, **_kwargs):
                        return await result_for("details", {"ok": True, "data": {}})

                    with patch.object(cave, "request_cave_treasure_miniapp_init_data", new=AsyncMock(side_effect=init)), \
                            patch.object(cave, "run_cave_dwelling_start_production_flow", new=AsyncMock(side_effect=start)), \
                            patch.object(cave, "run_cave_dwelling_snapshot_production_flow", new=AsyncMock(side_effect=details)), \
                            patch.object(cave, "_resolve_dwelling_player_id", return_value=self.identity_id), \
                            patch.object(cave, "_record_cave_entry_safe_directory") as directory:
                        result = await cave._load_cave_public_identity_session(
                            self.identity_id, "df_TEST", self.url, now=self.now,
                            capture_source="fixture", include_details=True,
                        )
                    self.assertFalse(result["ok"])
                    expected = ["init", "initial", "selected", "details"]
                    self.assertEqual(expected[:expected.index(boundary) + 1], steps)
                    directory.assert_not_called()

