import asyncio
import copy
import inspect
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


class _ControlledSendClient(_FakeClient):
    def __init__(self, *, error=None):
        super().__init__([])
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.error = error

    async def __call__(self, request):
        self.sent_requests.append(request)
        msg_id = 910000 + len(self.sent_requests)
        self.started.set()
        try:
            await self.release.wait()
            if self.error is not None:
                raise self.error
            return SimpleNamespace(id=msg_id)
        finally:
            self.finished.set()


class RuntimeSendTimeoutTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._send_tasks_snapshot = dict(runtime._GAME_SEND_TASKS)
        runtime._GAME_SEND_TASKS.clear()
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
            copy.deepcopy(runtime._GAME_GROUP_BOT_ACTIVITY_AT),
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
        runtime._GAME_GROUP_BOT_ACTIVITY_AT.clear()

    async def asyncTearDown(self):
        tasks = list(runtime._GAME_SEND_TASKS)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def tearDown(self):
        runtime._GAME_SEND_TASKS.clear()
        runtime._GAME_SEND_TASKS.update(self._send_tasks_snapshot)
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
        runtime._GAME_GROUP_BOT_ACTIVITY_AT.clear()
        runtime._GAME_GROUP_BOT_ACTIVITY_AT.update(copy.deepcopy(self._queue_snapshot[10]))
        runtime._ACCOUNT_RPC_LOCKS.clear()
        runtime._ACCOUNT_RPC_LOCKS.update(self._queue_snapshot[11])
        runtime.set_game_send_quiesced(self._queue_snapshot[12])
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

    def test_receipt_exposes_dispatch_time_without_replacing_receipt_time(self):
        identity_id = 301299112
        identity = state_module.ensure_identity_registered(identity_id)
        with (
            patch.object(runtime, "_append_sent_message_log"),
            patch.object(runtime, "_notify_game_command_sent_observers"),
            patch.object(runtime, "_reply_chain_tracker", {}),
            patch.object(runtime, "note_game_command_sent"),
        ):
            msg = runtime._finalize_game_command_sent(
                ".timing_test", msg_id=123, sent_at=110, send_started_at=100,
                send_as_id=identity_id, game_group_id=-1001, reply_timeout=60,
            )
        self.assertEqual(110, msg.sent_at)
        self.assertEqual(100, msg.send_started_at)
        pending = identity["pending_tasks"][(-1001, 123)]
        self.assertEqual(110, pending["sent_at"])
        self.assertEqual(100, pending["send_started_at"])
        self.assertEqual(60, pending["timeout"])

    async def test_rpc_records_dispatch_time_when_it_actually_starts(self):
        state_module.ensure_identity_registered(301299112)

        async def transport():
            return SimpleNamespace(id=123)

        with patch.object(runtime.time, "time", return_value=100):
            task, receipt = runtime._start_game_send_rpc(
                transport, account_id=7001, command=".timing_test", send_as_id=301299112,
                send_started_at=90, send_intent={},
            )
            await task
        self.assertTrue(receipt["started"])
        self.assertEqual(100, receipt["finalize_kwargs"]["send_started_at"])

    def _prepared_send_context(self, client):
        send_as_id = 301299112
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, 7001)
        stack = ExitStack()
        for patcher in (
            patch.object(runtime, "get_registered_client", return_value=client),
            patch.object(runtime, "is_account_offline", return_value=False),
            patch.object(runtime, "get_game_group_id", return_value=123456),
            patch.object(runtime, "get_game_group_ids", return_value=[123456]),
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
        return stack

    async def test_stop_and_pause_during_preparation_prevent_the_actual_send(self):
        for stopping in ("quiesce", "pause"):
            with self.subTest(stopping=stopping):
                runtime.set_game_send_quiesced(False)
                enabled = [True]
                client = _FakeClient(["ok"])
                original_resolve = client.get_input_entity

                async def resolve(entity_id):
                    result = await original_resolve(entity_id)
                    if entity_id == 301299112:
                        if stopping == "quiesce":
                            runtime.set_game_send_quiesced(True)
                        else:
                            enabled[0] = False
                    return result

                client.get_input_entity = resolve
                with self._prepared_send_context(client), patch.object(
                    runtime, "get_global_enabled", side_effect=lambda: enabled[0],
                ):
                    result = await runtime.send_game_command(".boundary_test", send_as_id=301299112)

                self.assertIsNone(result)
                self.assertEqual([], client.sent_requests)
                block = runtime.classify_game_send_block(301299112, ".boundary_test")
                self.assertEqual("unsent", block["status"])
                self.assertEqual("supervisor_quiesce" if stopping == "quiesce" else "global_disabled", block["code"])

    async def test_route_protection_is_rechecked_after_entity_resolution(self):
        client = _FakeClient(["ok"])
        prepared = [False]
        original_resolve = client.get_input_entity

        async def resolve(entity_id):
            result = await original_resolve(entity_id)
            if entity_id == 301299112:
                prepared[0] = True
            return result

        async def guard(*_args, **_kwargs):
            return (False, "protection consumed", "pre_send_guard") if prepared[0] else (True, "", "")

        client.get_input_entity = resolve
        with self._prepared_send_context(client), patch.object(
            runtime, "_run_game_command_pre_send_guards", side_effect=guard,
        ):
            result = await runtime.send_game_command(".boundary_test", send_as_id=301299112)
        self.assertIsNone(result)
        self.assertEqual([], client.sent_requests)

    async def test_changed_identity_owner_cannot_send_after_preparation_or_dispatch_queue(self):
        identity_id = 301299112
        expected_codes = {
            "removed": "identity_unavailable", "replaced": "identity_replaced",
            "rebound": "identity_account_changed", "disabled": "identity_disabled",
        }
        for boundary in ("entity", "guard", "dispatch"):
            for change, code in expected_codes.items():
                with self.subTest(boundary=boundary, change=change):
                    client = _FakeClient(["ok"])
                    prepared = False
                    changed = False
                    original_resolve = client.get_input_entity
                    original_start = runtime._start_game_send_rpc

                    def change_owner():
                        nonlocal changed
                        if changed:
                            return
                        changed = True
                        if change in {"removed", "replaced"}:
                            state_module.remove_identity(identity_id)
                            if change == "replaced":
                                state_module.ensure_identity_registered(identity_id)
                                state_module.set_identity_account(identity_id, 7001)
                        elif change == "rebound":
                            state_module.set_identity_account(identity_id, 7002)
                        else:
                            state_module.update_send_as_profile(identity_id, enabled=False)

                    async def resolve(entity_id):
                        nonlocal prepared
                        result = await original_resolve(entity_id)
                        if entity_id == identity_id:
                            prepared = True
                            if boundary == "entity":
                                change_owner()
                        return result

                    async def guard(*_args, **_kwargs):
                        if prepared and boundary == "guard":
                            await asyncio.sleep(0)
                            change_owner()
                        return True, "", ""

                    def start(*args, **kwargs):
                        result = original_start(*args, **kwargs)
                        if boundary == "dispatch":
                            change_owner()
                        return result

                    client.get_input_entity = resolve
                    with (
                        self._prepared_send_context(client),
                        patch.object(runtime, "_run_game_command_pre_send_guards", side_effect=guard),
                        patch.object(runtime, "_start_game_send_rpc", side_effect=start),
                    ):
                        state_module.update_send_as_profile(identity_id, enabled=True)
                        result = await runtime.send_game_command(".owner_boundary", send_as_id=identity_id)
                    self.assertTrue(changed)
                    self.assertEqual([], client.sent_requests)
                    self.assertIsNone(result)
                    block = runtime.classify_game_send_block(identity_id, ".owner_boundary")
                    self.assertEqual("unsent", block["status"])
                    self.assertEqual(code, block["code"])

    async def test_deleted_implicit_identity_context_cannot_fall_back_to_another_role(self):
        identity_id, other_id = 301299112, 301299113
        client = _FakeClient(["ok"])
        with self._prepared_send_context(client):
            state_module.ensure_identity_registered(other_id)
            state_module.set_identity_account(other_id, 7001)
            with state_module.use_identity(identity_id):
                state_module.remove_identity(identity_id)
                result = await runtime.send_game_command(".deleted_context")
        self.assertIsNone(result)
        self.assertEqual([], client.sent_requests)
        self.assertFalse(state_module.has_identity(identity_id))
        self.assertEqual({}, state_module.get_identity_state(other_id)["pending_tasks"])

    async def test_nanlong_queued_operation_is_revalidated_before_transport(self):
        from model.features import nanlong

        identity_id = 301299112
        for boundary in ("entity", "guard", "dispatch"):
            for change in ("disabled", "new_prompt", "cleared", "choice", "expired"):
                with self.subTest(boundary=boundary, change=change):
                    client = _FakeClient(["ok"])
                    prepared = changed = False
                    clock = 1788748200.0
                    original_resolve = client.get_input_entity
                    original_start = runtime._start_game_send_rpc

                    def change_operation():
                        nonlocal changed, clock
                        if changed:
                            return
                        changed = True
                        if change == "disabled":
                            identity["nanlong_enabled"] = False
                        elif change == "new_prompt":
                            nanlong._set_nanlong_pending(456, clock + 300, clock, chat_id=123456)
                        elif change == "cleared":
                            nanlong.clear_nanlong_state()
                        elif change == "choice":
                            state_module.set_nanlong_choice(identity_id, "exchange_gongfa")
                        else:
                            clock += 181

                    async def resolve(entity_id):
                        nonlocal prepared
                        result = await original_resolve(entity_id)
                        if entity_id == identity_id:
                            prepared = True
                            if boundary == "entity":
                                change_operation()
                        return result

                    async def guard(*_args, **_kwargs):
                        if prepared and boundary == "guard":
                            await asyncio.sleep(0)
                            change_operation()
                        return True, "", ""

                    def start(*args, **kwargs):
                        result = original_start(*args, **kwargs)
                        if boundary == "dispatch":
                            change_operation()
                        return result

                    client.get_input_entity = resolve
                    with (
                        self._prepared_send_context(client),
                        state_module.use_identity(identity_id) as identity,
                        patch.object(nanlong, "save_state", return_value=True),
                        patch.object(nanlong, "send_audit_log", new=AsyncMock()),
                        patch.object(nanlong.time, "time", side_effect=lambda: clock),
                        patch.object(runtime, "_run_game_command_pre_send_guards", side_effect=guard),
                        patch.object(runtime, "_start_game_send_rpc", side_effect=start),
                    ):
                        state_module.update_send_as_profile(identity_id, enabled=True, nanlong_choice="exchange_fabao")
                        identity.update(nanlong_enabled=True, concubine_name="南宫婉")
                        nanlong._set_nanlong_pending(123, clock + 180, clock, chat_id=123456)
                        identity["nanlong_reply_due_at"] = clock
                        await nanlong.run_nanlong_scheduler(clock)
                        block = runtime.classify_game_send_block(identity_id, nanlong.CMD_NANLONG_EXCHANGE_FABAO)
                    self.assertTrue(changed)
                    self.assertEqual([], client.sent_requests)
                    self.assertEqual("unsent", block["status"])
                    if change == "new_prompt":
                        self.assertEqual(456, identity["nanlong_reply_to_msg_id"])
                    self.assertEqual(0, identity["nanlong_last_msg_id"])

    async def test_tianxing_queued_step_is_revalidated_through_runtime(self):
        from model.features import tianxing

        identity_id = 301299112
        for boundary in ("entity", "guard", "dispatch"):
            for change in ("disabled", "config", "new_plan", "new_step", "observation", "paused", "unchanged"):
                with self.subTest(boundary=boundary, change=change):
                    client = _FakeClient(["ok"])
                    prepared = changed = False
                    now = 1788748200.0
                    original_resolve = client.get_input_entity
                    original_start = runtime._start_game_send_rpc

                    def change_operation():
                        nonlocal changed
                        if changed:
                            return
                        changed = True
                        timeline = identity["tianxing_timeline_state"]
                        if change == "disabled":
                            identity["tianxing_enabled"] = False
                        elif change == "config":
                            identity["tianxing_auto_config"]["auto_predict_enabled"] = False
                        elif change == "new_plan":
                            timeline["plan_id"] = "new-plan"
                        elif change == "new_step":
                            timeline["active_step"]["send_started_at"] = now + 1
                        elif change == "observation":
                            identity["tianxing_observation"].update(
                                current_prediction="斗法", current_prediction_until=now + 3600,
                            )
                        elif change == "paused":
                            identity["tianxing_observation"]["automation_paused_until"] = -1

                    async def resolve(entity_id):
                        nonlocal prepared
                        result = await original_resolve(entity_id)
                        if entity_id == identity_id:
                            prepared = True
                            if boundary == "entity":
                                change_operation()
                        return result

                    async def guard(*_args, **_kwargs):
                        if prepared and boundary == "guard":
                            await asyncio.sleep(0)
                            change_operation()
                        return True, "", ""

                    def start(*args, **kwargs):
                        result = original_start(*args, **kwargs)
                        if boundary == "dispatch":
                            change_operation()
                        return result

                    client.get_input_entity = resolve
                    with (
                        self._prepared_send_context(client),
                        state_module.use_identity(identity_id) as identity,
                        patch.object(tianxing, "save_state", return_value=True),
                        patch.object(tianxing, "_TIANXING_TIMELINE_LOCKS", {}),
                        patch.object(tianxing, "_tianxing_action_guard_wait", return_value=(0, "")),
                        patch.object(tianxing.time, "time", return_value=now),
                        patch.object(runtime, "_run_game_command_pre_send_guards", side_effect=guard),
                        patch.object(runtime, "_start_game_send_rpc", side_effect=start),
                    ):
                        state_module.update_send_as_profile(identity_id, enabled=True, sect_name="天星宗")
                        state_module.set_global_enabled(True)
                        identity.update(
                            tianxing_enabled=True,
                            tianxing_auto_config={"auto_predict_enabled": True},
                            tianxing_observation={"last_observed_at": now - 1, "tianji_value": 12},
                        )
                        step = {"action": "predict", "arg": "探索", "status": "pending"}
                        identity["tianxing_timeline_state"] = {
                            "plan_id": "old-plan", "phase": "waiting_send", "active_step_index": 0,
                            "active_step": step, "steps": [dict(step)],
                        }
                        await tianxing.run_tianxing_timeline_scheduler(now)
                        block = runtime.classify_game_send_block(identity_id, ".推命 探索")
                    self.assertTrue(changed)
                    self.assertEqual(1 if change == "unchanged" else 0, len(client.sent_requests))
                    if change != "unchanged":
                        self.assertEqual("unsent", block["status"])
                    else:
                        self.assertEqual(123456, identity["tianxing_timeline_state"]["active_step"]["send_chat_id"])

    async def test_nanlong_all_steps_keep_normal_dispatch_and_honor_disable(self):
        from model.features import nanlong

        identity_id = 301299112
        commands = {
            "exchange": nanlong.CMD_NANLONG_EXCHANGE_FABAO,
            "place": nanlong.CMD_CONCUBINE_PLACE,
            "reject": nanlong.CMD_NANLONG_REJECT,
            "recall": nanlong.CMD_CONCUBINE_RECALL,
        }
        for step, command in commands.items():
            for disable in (False, True):
                with self.subTest(step=step, disable=disable):
                    client = _FakeClient(["ok"])
                    original_resolve = client.get_input_entity
                    clock = 1788748200.0

                    async def resolve(entity_id):
                        result = await original_resolve(entity_id)
                        if disable and entity_id == identity_id:
                            identity["nanlong_enabled"] = False
                        elif step == "recall" and entity_id == identity_id:
                            state_module.set_nanlong_choice(identity_id, "reject")
                        return result

                    client.get_input_entity = resolve
                    with (
                        self._prepared_send_context(client),
                        state_module.use_identity(identity_id) as identity,
                        patch.object(nanlong, "save_state", return_value=True),
                        patch.object(nanlong, "send_audit_log", new=AsyncMock()),
                        patch.object(nanlong.time, "time", return_value=clock),
                        patch.object(nanlong, "_get_nanlong_cave_status", return_value=nanlong.NANLONG_CAVE_STATUS_AVAILABLE),
                        patch.object(nanlong, "iter_message_log_entries_between", return_value=[]),
                    ):
                        state_module.update_send_as_profile(identity_id, enabled=True, nanlong_choice="reject" if step == "reject" else "exchange_fabao")
                        identity.update(nanlong_enabled=True, concubine_name="墨彩环" if step == "place" else "南宫婉")
                        nanlong._set_nanlong_pending(123, clock + 180, clock, chat_id=123456)
                        identity["nanlong_reply_due_at"] = clock
                        if step == "recall":
                            identity.update(nanlong_last_chat_id=123456, nanlong_last_msg_id=124)
                            await nanlong._send_nanlong_recall_after_trade(clock)
                        else:
                            await nanlong.run_nanlong_scheduler(clock)
                    self.assertEqual([] if disable else [command], [request.message for request in client.sent_requests])

    async def test_nanlong_choice_change_after_dispatch_preserves_the_actual_receipt(self):
        from model.features import nanlong

        identity_id = 301299112

        class ChangeChoiceClient(_FakeClient):
            async def __call__(self, request):
                state_module.set_nanlong_choice(identity_id, "exchange_gongfa")
                return await super().__call__(request)

        client = ChangeChoiceClient(["ok"])
        now = runtime.time.time()
        with (
            self._prepared_send_context(client),
            state_module.use_identity(identity_id) as identity,
            patch.object(nanlong, "save_state", return_value=True),
            patch.object(nanlong, "send_audit_log", new=AsyncMock()),
            patch.object(nanlong, "iter_message_log_entries_between", return_value=[]),
        ):
            state_module.update_send_as_profile(identity_id, enabled=True, nanlong_choice="exchange_fabao")
            identity.update(nanlong_enabled=True, concubine_name="南宫婉")
            nanlong._set_nanlong_pending(123, now + 180, now, chat_id=123456)
            identity["nanlong_reply_due_at"] = now
            await nanlong.run_nanlong_scheduler(now)
        self.assertEqual([nanlong.CMD_NANLONG_EXCHANGE_FABAO], [request.message for request in client.sent_requests])
        self.assertEqual(910001, identity["nanlong_last_msg_id"])
        self.assertEqual(nanlong.CMD_NANLONG_EXCHANGE_FABAO, identity["nanlong_last_command"])
        self.assertEqual("exchange_gongfa", state_module.get_nanlong_choice(identity_id))

    async def test_operation_check_requires_explicit_synchronous_success(self):
        async def async_check():
            return True

        def raises():
            raise RuntimeError("private-value")

        for mode in ("true", "false", "none", "number", "dict", "async", "future", "raises"):
            with self.subTest(mode=mode):
                client = _FakeClient(["ok"])
                coroutine = async_check()
                future = asyncio.get_running_loop().create_future()
                future.set_result(True)
                decision = {"true": True, "false": False, "none": None, "number": 1, "dict": {}, "async": coroutine, "future": future}.get(mode)
                with self._prepared_send_context(client):
                    result = await runtime.send_game_command(
                        ".operation_test", send_as_id=301299112,
                        operation_check=raises if mode == "raises" else lambda: decision,
                    )
                if mode == "async":
                    self.assertEqual(inspect.CORO_CLOSED, inspect.getcoroutinestate(coroutine))
                coroutine.close()
                if mode == "true":
                    self.assertIsNotNone(result)
                    self.assertEqual(1, len(client.sent_requests))
                    self.assertNotIn("operation_check", vars(result))
                    pending = state_module.get_identity_state(301299112)["pending_tasks"][(123456, 910001)]
                    self.assertNotIn("operation_check", pending)
                else:
                    self.assertIsNone(result)
                    self.assertEqual([], client.sent_requests)
                    block = runtime.classify_game_send_block(301299112, ".operation_test")
                    self.assertEqual("unsent", block["status"])
                    self.assertNotIn("private-value", str(block))

    async def test_explicit_send_for_already_disabled_identity_keeps_existing_manual_behavior(self):
        identity_id = 301299112
        client = _FakeClient(["ok"])
        with self._prepared_send_context(client):
            state_module.update_send_as_profile(identity_id, enabled=False)
            result = await runtime.send_game_command(".manual_owner_read", send_as_id=identity_id, track=False)
        self.assertIsNotNone(result)
        self.assertEqual(1, len(client.sent_requests))

    async def test_identity_disabled_after_rpc_started_retains_the_real_send_receipt(self):
        identity_id = 301299112
        client = _ControlledSendClient()
        with self._prepared_send_context(client):
            task = asyncio.create_task(runtime.send_game_command(".already_dispatched", send_as_id=identity_id))
            await asyncio.wait_for(client.started.wait(), timeout=1)
            state_module.update_send_as_profile(identity_id, enabled=False)
            client.release.set()
            result = await task
        self.assertIsNotNone(result)
        self.assertEqual(1, len(client.sent_requests))
        self.assertIn((123456, result.id), state_module.get_identity_state(identity_id)["pending_tasks"])

    async def test_global_pause_between_rpc_task_creation_and_dispatch_is_unsent(self):
        client = _FakeClient(["ok"])
        enabled = True
        original_start = runtime._start_game_send_rpc

        def start(*args, **kwargs):
            nonlocal enabled
            result = original_start(*args, **kwargs)
            enabled = False
            return result

        with (
            self._prepared_send_context(client),
            patch.object(runtime, "get_global_enabled", side_effect=lambda: enabled),
            patch.object(runtime, "_start_game_send_rpc", side_effect=start),
        ):
            result = await runtime.send_game_command(".dispatch_pause", send_as_id=301299112)
        self.assertIsNone(result)
        self.assertEqual([], client.sent_requests)
        self.assertEqual("global_disabled", runtime.classify_game_send_block(301299112, ".dispatch_pause")["code"])

    async def test_raising_send_guard_blocks_transport_as_definitely_unsent(self):
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous):
                client = _FakeClient(["ok"])

                def broken_guard(*_args, **_kwargs):
                    raise ValueError("invalid protection state")

                async def async_broken_guard(*_args, **_kwargs):
                    await asyncio.sleep(0)
                    raise ValueError("invalid protection state")

                guard = async_broken_guard if asynchronous else broken_guard
                with (
                    self._prepared_send_context(client),
                    patch.object(runtime, "_GAME_COMMAND_PRE_SEND_GUARDS", [guard]),
                    patch.object(runtime, "_GAME_PRE_SEND_GUARD_BLOCK_LAST", {}),
                    patch.object(runtime.traceback, "print_exc"),
                ):
                    result = await runtime.send_game_command(".guard_failure", send_as_id=301299112)
                self.assertIsNone(result)
                self.assertEqual([], client.sent_requests)
                block = runtime.classify_game_send_block(301299112, ".guard_failure")
                self.assertEqual("unsent", block["status"])
                self.assertEqual("pre_send_guard", block["code"])
                self.assertEqual({}, state_module.get_identity_state(301299112)["pending_tasks"])

    async def test_send_guard_future_is_awaited_before_transport(self):
        client = _FakeClient(["ok"])
        decision = asyncio.get_running_loop().create_future()
        decision.set_result({"allowed": False, "code": "pre_send_guard", "reason": "route changed"})
        with (
            self._prepared_send_context(client),
            patch.object(runtime, "_GAME_COMMAND_PRE_SEND_GUARDS", [lambda *_args, **_kwargs: decision]),
            patch.object(runtime, "_GAME_PRE_SEND_GUARD_BLOCK_LAST", {}),
        ):
            result = await runtime.send_game_command(".guard_future", send_as_id=301299112)
        self.assertIsNone(result)
        self.assertEqual([], client.sent_requests)
        self.assertEqual("unsent", runtime.classify_game_send_block(301299112, ".guard_future")["status"])

    async def test_send_guard_result_normalization_failure_is_unsent(self):
        class InvalidDecision:
            def __bool__(self):
                raise ValueError("unreadable decision")

        client = _FakeClient(["ok"])
        with (
            self._prepared_send_context(client),
            patch.object(runtime, "_GAME_COMMAND_PRE_SEND_GUARDS", [lambda *_args, **_kwargs: {"allowed": InvalidDecision()}]),
            patch.object(runtime, "_GAME_PRE_SEND_GUARD_BLOCK_LAST", {}),
            patch.object(runtime.traceback, "print_exc"),
        ):
            result = await runtime.send_game_command(".guard_invalid_decision", send_as_id=301299112)
        self.assertIsNone(result)
        self.assertEqual([], client.sent_requests)
        self.assertEqual("unsent", runtime.classify_game_send_block(301299112, ".guard_invalid_decision")["status"])

    async def test_cancelled_send_guard_is_not_converted_to_permission(self):
        async def guard(*_args, **_kwargs):
            raise asyncio.CancelledError()

        with patch.object(runtime, "_GAME_COMMAND_PRE_SEND_GUARDS", [guard]):
            with self.assertRaises(asyncio.CancelledError):
                await runtime._run_game_command_pre_send_guards(".guard_cancel", send_as_id=301299112, priority="normal")

    async def test_cancel_while_waiting_account_lock_closes_unstarted_coroutine(self):
        async def operation():
            self.fail("cancelled queued operation must not run")

        operation_coro = operation()
        async with runtime.account_rpc_slot(account_id=7001):
            task = asyncio.create_task(runtime._run_account_rpc(operation_coro, account_id=7001))
            await asyncio.sleep(0)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        try:
            self.assertEqual(inspect.CORO_CLOSED, inspect.getcoroutinestate(operation_coro))
        finally:
            operation_coro.close()

    async def test_cancelled_sender_retains_and_registers_late_rpc_result_once(self):
        client = _ControlledSendClient()
        with self._prepared_send_context(client):
            task = asyncio.create_task(runtime.send_game_command(
                ".cancel_test", send_as_id=301299112, source_module="audit",
                op_id="cancel-op", chain_id="cancel-chain", max_retry=2,
            ))
            await asyncio.wait_for(client.started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            block = runtime.classify_game_send_block(301299112, ".cancel_test")
            client.release.set()
            await asyncio.wait_for(client.finished.wait(), 1)
            for _ in range(8):
                await asyncio.sleep(0)
            pending = state_module.get_identity_state(301299112)["pending_tasks"].get((123456, 910001))
            self.assertIsNotNone(pending)
            self.assertEqual("unknown", block["status"])
            self.assertEqual(0, pending["max_retry"])
            self.assertEqual("cancel-op", pending["op_id"])
            self.assertEqual("cancel-chain", pending["chain_id"])
            self.assertEqual(123456, pending["chat_id"])
            runtime._append_sent_message_log.assert_called_once()
            runtime._notify_game_command_sent_observers.assert_called_once()
            self.assertEqual("none", runtime.classify_game_send_block(301299112, ".cancel_test")["status"])
        self.assertEqual(1, len(client.sent_requests))

    async def test_result_after_timeout_return_is_still_registered(self):
        client = _ControlledSendClient()
        with (
            self._prepared_send_context(client),
            patch.object(runtime, "GAME_SEND_RPC_TIMEOUT_SEC", 0.01),
            patch.object(runtime, "GAME_SEND_TIMEOUT_RECOVERY_WAIT_SEC", 0),
            patch.object(runtime, "recover_sent_command_from_message_log", return_value=None),
            patch.object(runtime, "recover_sent_command_from_reply_log", return_value=None),
        ):
            result = await runtime.send_game_command(".late_test", send_as_id=301299112)
            self.assertIsNone(result)
            client.release.set()
            await asyncio.wait_for(client.finished.wait(), 1)
            for _ in range(8):
                await asyncio.sleep(0)
            self.assertIn((123456, 910001), state_module.get_identity_state(301299112)["pending_tasks"])
            runtime._append_sent_message_log.assert_called_once()

    async def test_cancellation_before_rpc_is_classified_unsent(self):
        client = _FakeClient(["ok"])
        preparing = asyncio.Event()

        async def resolve(_entity_id):
            preparing.set()
            await asyncio.Event().wait()

        client.get_input_entity = resolve
        with self._prepared_send_context(client):
            task = asyncio.create_task(runtime.send_game_command(".cancel_before_send", send_as_id=301299112))
            await asyncio.wait_for(preparing.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual("unsent", runtime.classify_game_send_block(301299112, ".cancel_before_send")["status"])
        self.assertEqual([], client.sent_requests)

    async def test_abandoned_rpc_keeps_next_game_send_serialized(self):
        client = _ControlledSendClient()
        with self._prepared_send_context(client):
            first = asyncio.create_task(runtime.send_game_command(".first_test", send_as_id=301299112))
            await asyncio.wait_for(client.started.wait(), 1)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(runtime.send_game_command(".second_test", send_as_id=301299112))
            try:
                for _ in range(12):
                    await asyncio.sleep(0)
                self.assertEqual(1, len(client.sent_requests))
            finally:
                client.release.set()
                await asyncio.wait_for(second, 1)
        self.assertEqual(2, len(client.sent_requests))

    async def test_recovered_log_and_late_rpc_do_not_double_register(self):
        client = _ControlledSendClient()
        with (
            self._prepared_send_context(client),
            patch.object(runtime, "GAME_SEND_RPC_TIMEOUT_SEC", 0.01),
            patch.object(runtime, "GAME_SEND_TIMEOUT_RECOVERY_WAIT_SEC", 0),
            patch.object(runtime, "recover_sent_command_from_message_log", return_value={
                "event_type": "message", "message_id": 910001, "ts_epoch": 1234.5,
            }),
        ):
            result = await runtime.send_game_command(".recover_once", send_as_id=301299112)
            self.assertEqual(910001, result.id)
            client.release.set()
            await asyncio.wait_for(client.finished.wait(), 1)
            for _ in range(8):
                await asyncio.sleep(0)
            runtime._append_sent_message_log.assert_called_once()
            runtime._notify_game_command_sent_observers.assert_called_once()

    async def test_cancel_before_transport_dispatch_does_not_send(self):
        client = _FakeClient(["ok"])
        original_start = runtime._start_game_send_rpc

        def start_then_cancel(*args, **kwargs):
            result = original_start(*args, **kwargs)
            asyncio.current_task().cancel()
            return result

        with (
            self._prepared_send_context(client),
            patch.object(runtime, "_start_game_send_rpc", side_effect=start_then_cancel),
        ):
            task = asyncio.create_task(runtime.send_game_command(".cancel_dispatch", send_as_id=301299112))
            with self.assertRaises(asyncio.CancelledError):
                await task
            for _ in range(8):
                await asyncio.sleep(0)
            self.assertEqual([], client.sent_requests)
            self.assertEqual("unsent", runtime.classify_game_send_block(301299112, ".cancel_dispatch")["status"])

    async def test_detached_success_blocks_a_queued_duplicate(self):
        client = _ControlledSendClient()
        with self._prepared_send_context(client):
            first = asyncio.create_task(runtime.send_game_command(".same_test", send_as_id=301299112))
            await asyncio.wait_for(client.started.wait(), 1)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            duplicate = asyncio.create_task(runtime.send_game_command(".same_test", send_as_id=301299112))
            client.release.set()
            self.assertIsNone(await asyncio.wait_for(duplicate, 1))
            self.assertEqual(1, len(client.sent_requests))
            with (
                patch.object(runtime, "should_pause_for_bot_health", return_value=False),
                patch.object(runtime, "get_bot_last_seen_at", return_value=runtime.time.time() + 10_000),
                patch.object(runtime, "find_message_log_replies", return_value=[]),
            ):
                await runtime.run_retry_scheduler(runtime.time.time() + 5000, send_as_id=301299112)
        self.assertTrue(state_module.get_identity_state(301299112)["pending_tasks"][(123456, 910001)]["send_caller_detached"])

    async def test_detached_untracked_success_keeps_no_retry_pending_and_blocks_duplicate(self):
        client = _ControlledSendClient()
        with self._prepared_send_context(client):
            first = asyncio.create_task(runtime.send_game_command(".untracked_once", track=False, send_as_id=301299112))
            await asyncio.wait_for(client.started.wait(), 1)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            duplicate = asyncio.create_task(runtime.send_game_command(".untracked_once", track=False, send_as_id=301299112))
            client.release.set()
            result = await asyncio.wait_for(duplicate, 1)
            self.assertIsNone(result)
            self.assertEqual(1, len(client.sent_requests))
            pending = state_module.get_identity_state(301299112)["pending_tasks"].get((123456, 910001))
            self.assertIsNotNone(pending)
            self.assertEqual(0, pending["max_retry"])
            self.assertTrue(pending["send_caller_detached"])

    async def test_bot_silence_does_not_discard_detached_send_ownership(self):
        identity_id = 301299112
        identity = state_module.ensure_identity_registered(identity_id)
        now = runtime.time.time()
        sent_at = now - runtime.BOT_SILENCE_TIMEOUT_SEC - 1
        identity["pending_tasks"][(123456, 910001)] = {
            "cmd": ".uncertain_once", "sent_at": sent_at, "chat_id": 123456,
            "retry": 0, "max_retry": 0, "timeout": 1, "send_caller_detached": True,
        }
        with (
            patch.object(runtime, "should_pause_for_bot_health", return_value=False),
            patch.object(runtime, "get_bot_last_seen_at", return_value=sent_at - 10),
            patch.object(runtime, "_recover_pending_reply_from_message_log", new=AsyncMock(return_value=None)),
            patch.object(runtime, "mark_bot_health_suspect") as suspect,
            patch.object(runtime, "send_game_command", new=AsyncMock()) as sender,
        ):
            await runtime.run_retry_scheduler(now, send_as_id=identity_id)
        sender.assert_not_awaited()
        suspect.assert_called_once()
        self.assertIn((123456, 910001), identity["pending_tasks"])
        self.assertTrue(identity["pending_tasks"][(123456, 910001)]["send_caller_detached"])

    def test_module_pending_cleanup_preserves_detached_send_evidence(self):
        identity_id = 301299112
        identity = state_module.ensure_identity_registered(identity_id)
        identity["pending_tasks"] = {
            (123456, 910001): {"cmd": ".pending_once", "send_caller_detached": True},
            (123456, 910002): {"cmd": ".pending_once"},
        }
        removed = runtime.clear_pending_tasks_by_commands({".pending_once"}, send_as_id=identity_id)
        self.assertEqual([910002], removed)
        self.assertEqual({(123456, 910001)}, set(identity["pending_tasks"]))

    async def test_retry_scheduler_does_not_expire_new_work_during_reply_recovery(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                identity_id = 301299112
                identity = state_module.ensure_identity_registered(identity_id)
                now = runtime.time.time()
                key = (123456, 910001)
                item = {"cmd": ".old_pending", "sent_at": now - runtime.BOT_SILENCE_TIMEOUT_SEC - 1, "timeout": 1}
                identity["pending_tasks"][key] = item
                fresh = {"cmd": ".fresh_pending", "sent_at": now + 100, "timeout": 60}

                async def recover(*_args, **_kwargs):
                    if replace:
                        identity["pending_tasks"][key] = dict(fresh)
                    else:
                        item.update(fresh)
                    return None

                with (
                    patch.object(runtime, "should_pause_for_bot_health", return_value=False),
                    patch.object(runtime, "get_bot_last_seen_at", return_value=0),
                    patch.object(runtime, "_recover_pending_reply_from_message_log", new=AsyncMock(side_effect=recover)),
                    patch.object(runtime, "mark_bot_health_suspect"),
                    patch.object(runtime, "send_game_command", new=AsyncMock()) as sender,
                ):
                    await runtime.run_retry_scheduler(now, send_as_id=identity_id)
                sender.assert_not_awaited()
                self.assertEqual(fresh, identity["pending_tasks"].get(key))

    async def test_retry_scheduler_stops_when_identity_is_removed_during_recovery(self):
        identity_id = 301299112
        identity = state_module.ensure_identity_registered(identity_id)
        now = runtime.time.time()
        identity["pending_tasks"][(123456, 910001)] = {
            "cmd": ".old_pending", "sent_at": now - runtime.BOT_SILENCE_TIMEOUT_SEC - 1, "timeout": 1,
        }

        async def recover(*_args, **_kwargs):
            state_module.remove_identity(identity_id)
            return None

        with (
            patch.object(runtime, "should_pause_for_bot_health", return_value=False),
            patch.object(runtime, "get_bot_last_seen_at", return_value=0),
            patch.object(runtime, "_recover_pending_reply_from_message_log", new=AsyncMock(side_effect=recover)),
            patch.object(runtime, "mark_bot_health_suspect"),
            patch.object(runtime, "send_game_command", new=AsyncMock()) as sender,
        ):
            await runtime.run_retry_scheduler(now, send_as_id=identity_id)
        sender.assert_not_awaited()
        self.assertFalse(state_module.has_identity(identity_id))

    async def test_timeout_notification_cannot_remove_new_pending_work(self):
        identity_id = 301299112
        identity = state_module.ensure_identity_registered(identity_id)
        now = runtime.time.time()
        key = (123456, 910001)
        identity["pending_tasks"][key] = {"cmd": ".old_pending", "sent_at": now - 100, "timeout": 10, "max_retry": 0}
        fresh = {"cmd": ".fresh_pending", "sent_at": now + 1, "timeout": 100}

        async def audit(*_args, **_kwargs):
            identity["pending_tasks"][key] = dict(fresh)

        with (
            patch.object(runtime, "should_pause_for_bot_health", return_value=False),
            patch.object(runtime, "get_bot_last_seen_at", return_value=now),
            patch.object(runtime, "_recover_pending_reply_from_message_log", new=AsyncMock(return_value=None)),
            patch.object(runtime, "send_audit_log", new=AsyncMock(side_effect=audit)),
        ):
            await runtime.run_retry_scheduler(now, send_as_id=identity_id)
        self.assertEqual(fresh, identity["pending_tasks"].get(key))

    async def test_retry_receipt_cannot_update_or_remove_replaced_pending_work(self):
        for accepted in (False, True):
            with self.subTest(accepted=accepted):
                identity_id = 301299112
                identity = state_module.ensure_identity_registered(identity_id)
                now = runtime.time.time()
                key = (123456, 910001)
                identity["pending_tasks"] = {key: {"cmd": ".old_pending", "sent_at": now - 100, "timeout": 10, "max_retry": 1}}
                fresh = {"cmd": ".fresh_pending", "sent_at": now + 1, "timeout": 100}

                async def send(command, **_kwargs):
                    result = None
                    if accepted:
                        result = runtime._finalize_game_command_sent(
                            command, msg_id=910002, sent_at=now,
                            send_as_id=identity_id, game_group_id=123456, append_sent_log=False,
                        )
                    identity["pending_tasks"][key] = dict(fresh)
                    return result

                with (
                    patch.object(runtime, "should_pause_for_bot_health", return_value=False),
                    patch.object(runtime, "get_bot_last_seen_at", return_value=now),
                    patch.object(runtime, "_recover_pending_reply_from_message_log", new=AsyncMock(return_value=None)),
                    patch.object(runtime, "send_game_command", new=AsyncMock(side_effect=send)),
                    patch.object(runtime, "_notify_game_command_sent_observers"),
                    patch.object(runtime, "_reply_chain_tracker", {}),
                ):
                    await runtime.run_retry_scheduler(now, send_as_id=identity_id)
                self.assertEqual(fresh, identity["pending_tasks"].get(key))
                if accepted:
                    pending = identity["pending_tasks"][(123456, 910002)]
                    self.assertEqual(0, pending["max_retry"])
                    self.assertTrue(pending["send_caller_detached"])

    async def test_failed_retry_preserves_original_send_time_and_waits_before_retry(self):
        identity_id = 301299112
        identity = state_module.ensure_identity_registered(identity_id)
        now = runtime.time.time()
        item = {"cmd": ".old_pending", "sent_at": now - 100, "timeout": 10, "max_retry": 1}
        identity["pending_tasks"] = {(123456, 910001): item}
        with (
            patch.object(runtime, "should_pause_for_bot_health", return_value=False),
            patch.object(runtime, "get_bot_last_seen_at", return_value=now),
            patch.object(runtime, "find_message_log_replies", return_value=[]),
            patch.object(runtime, "send_game_command", new=AsyncMock(return_value=None)) as sender,
            patch.object(runtime, "get_last_game_send_block", return_value={"code": "send_queue_timeout"}),
        ):
            await runtime.run_retry_scheduler(now, send_as_id=identity_id)
            await runtime.run_retry_scheduler(now + 2, send_as_id=identity_id)
        self.assertEqual(now - 100, item["sent_at"])
        sender.assert_awaited_once()

    async def test_old_refresh_timeout_cannot_clear_a_newer_refresh_anchor(self):
        identity_id = 301299112
        identity = state_module.ensure_identity_registered(identity_id)
        now = runtime.time.time()
        identity.update(last_identity_info_msg_id=910099, identity_info_reply_msg_ids=[910099], identity_info_followup_due_at=now + 100)
        identity["pending_tasks"] = {(123456, 910001): {
            "cmd": runtime.CMD_IDENTITY_INFO, "sent_at": now - 100, "timeout": 10,
            "retry": 1, "max_retry": 1,
        }}
        with (
            patch.object(runtime, "should_pause_for_bot_health", return_value=False),
            patch.object(runtime, "get_bot_last_seen_at", return_value=now),
            patch.object(runtime, "_recover_pending_reply_from_message_log", new=AsyncMock(return_value=None)),
            patch.object(runtime, "send_audit_log", new=AsyncMock()),
        ):
            await runtime.run_retry_scheduler(now, send_as_id=identity_id)
        self.assertEqual(910099, identity["last_identity_info_msg_id"])
        self.assertEqual([910099], identity["identity_info_reply_msg_ids"])
        self.assertEqual(now + 100, identity["identity_info_followup_due_at"])

    async def test_ambiguous_cross_chat_refresh_timeout_cannot_clear_the_anchor(self):
        identity_id = 301299112
        identity = state_module.ensure_identity_registered(identity_id)
        now = runtime.time.time()
        identity.update(last_identity_info_msg_id=910001, identity_info_reply_msg_ids=[910001])
        identity["my_msg_ids"] = {(123456, 910001): now - 100, (123457, 910001): now - 10}
        identity["pending_tasks"] = {(123456, 910001): {
            "cmd": runtime.CMD_IDENTITY_INFO, "sent_at": now - 100, "timeout": 10,
            "retry": 1, "max_retry": 1,
        }}
        with (
            patch.object(runtime, "should_pause_for_bot_health", return_value=False),
            patch.object(runtime, "get_bot_last_seen_at", return_value=now),
            patch.object(runtime, "_recover_pending_reply_from_message_log", new=AsyncMock(return_value=None)),
            patch.object(runtime, "send_audit_log", new=AsyncMock()),
        ):
            await runtime.run_retry_scheduler(now, send_as_id=identity_id)
        self.assertEqual(910001, identity["last_identity_info_msg_id"])
        self.assertEqual([910001], identity["identity_info_reply_msg_ids"])

    async def test_other_account_rpc_waits_for_cancelled_senders_transport(self):
        client = _ControlledSendClient()
        read_started = asyncio.Event()

        async def read():
            read_started.set()

        with self._prepared_send_context(client):
            first = asyncio.create_task(runtime.send_game_command(".read_barrier", send_as_id=301299112))
            await asyncio.wait_for(client.started.wait(), 1)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            reading = asyncio.create_task(runtime._run_account_rpc(read(), account_id=7001))
            try:
                for _ in range(8):
                    await asyncio.sleep(0)
                self.assertFalse(read_started.is_set())
            finally:
                client.release.set()
                await asyncio.wait_for(reading, 1)
        self.assertTrue(read_started.is_set())

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

    def test_sent_message_route_falls_back_to_full_daily_log(self):
        sent_at = 1_700_000_000.0
        with tempfile.TemporaryDirectory() as tmpdir, \
                patch.object(runtime, "MESSAGES_DIR", tmpdir), \
                patch.object(runtime, "cleanup_message_logs"):
            runtime._append_sent_message_log(
                920003,
                ".助阵",
                301299112,
                sent_at=sent_at,
                game_group_id=-1001680975844,
                topic_id=7310786,
            )
            log_path = next(Path(tmpdir).glob("*.log"))
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("x" * (600 * 1024))
            with patch.object(runtime, "_recent_sent_message_log_paths", return_value=[str(log_path)]):
                chat_id = runtime.get_sent_message_chat_id(920003, send_as_id=301299112)

        self.assertEqual(-1001680975844, chat_id)

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

    async def test_send_rpc_timeout_releases_lock_but_waits_for_transport_deadline(self):
        send_as_id = 301299112
        account_id = 7001
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        client = _FakeClient(["timeout", "ok"])

        with ExitStack() as stack:
            for patcher in (
                patch.object(runtime, "GAME_SEND_RPC_TIMEOUT_SEC", 0.05),
                patch.object(runtime, "GAME_SEND_RPC_COMPLETION_TIMEOUT_SEC", 0.1),
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

    def test_recent_bot_activity_promotes_only_active_listener_group_for_sends(self):
        send_as_id = 301299112
        account_id = 7001
        primary_group_id = -1002083016447
        alternate_group_id = -1001680975844
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        state_module.set_game_group_route_config({
            "enabled": True,
            "primary_group_id": primary_group_id,
            "backup_group_ids": [alternate_group_id],
            "topic_id_by_group": {str(primary_group_id): 0, str(alternate_group_id): 7310786},
        })
        now = 10_000.0
        runtime.note_game_group_bot_activity(primary_group_id, now=now - 900)
        runtime.note_game_group_bot_activity(alternate_group_id, now=now - 10)
        runtime._GAME_GROUP_BOT_ACTIVITY_AT.clear()

        routes = runtime._game_group_route_candidates(send_as_id, account_id, now=now)

        self.assertEqual(alternate_group_id, routes[0][0])
        snapshot = runtime.get_game_group_route_activity_snapshot(now=now)
        self.assertEqual([primary_group_id, alternate_group_id], snapshot["listen_group_ids"])
        self.assertEqual(primary_group_id, snapshot["preferred_send_group_id"])

    def test_preferred_group_stays_first_when_both_listener_groups_are_active(self):
        send_as_id = 301299112
        account_id = 7001
        primary_group_id = -1002083016447
        alternate_group_id = -1001680975844
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        state_module.set_game_group_route_config({
            "enabled": True,
            "primary_group_id": primary_group_id,
            "backup_group_ids": [alternate_group_id],
            "topic_id_by_group": {str(primary_group_id): 0, str(alternate_group_id): 7310786},
        })
        now = 10_000.0
        runtime.note_game_group_bot_activity(primary_group_id, now=now - 30)
        runtime.note_game_group_bot_activity(alternate_group_id, now=now - 5)

        routes = runtime._game_group_route_candidates(send_as_id, account_id, now=now)

        self.assertEqual(primary_group_id, routes[0][0])

    def test_identity_reply_group_hint_wins_over_global_primary_activity(self):
        send_as_id = 301299112
        account_id = 7001
        primary_group_id = -1002083016447
        alternate_group_id = -1001680975844
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        state_module.set_game_group_route_config({
            "enabled": True,
            "primary_group_id": primary_group_id,
            "backup_group_ids": [alternate_group_id],
            "topic_id_by_group": {str(primary_group_id): 0, str(alternate_group_id): 7310786},
        })
        now = 10_000.0
        runtime.note_game_group_bot_activity(primary_group_id, now=now - 5)
        runtime.note_game_group_bot_activity(alternate_group_id, now=now - 900)
        self.assertTrue(runtime.note_game_group_bot_reply(send_as_id, alternate_group_id, now=now))

        routes = runtime._game_group_route_candidates(send_as_id, account_id, now=now)

        self.assertEqual(alternate_group_id, routes[0][0])
        self.assertEqual(alternate_group_id, state_module.get_game_group_reply_route(send_as_id))

    def test_explicit_source_group_remains_authoritative_over_identity_hint(self):
        send_as_id = 301299112
        account_id = 7001
        primary_group_id = -1002083016447
        alternate_group_id = -1001680975844
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        state_module.set_game_group_route_config({
            "enabled": True,
            "primary_group_id": primary_group_id,
            "backup_group_ids": [alternate_group_id],
            "topic_id_by_group": {str(primary_group_id): 0, str(alternate_group_id): 7310786},
        })
        runtime.note_game_group_bot_reply(send_as_id, alternate_group_id, now=10_000.0)

        routes = runtime._game_group_route_candidates(
            send_as_id,
            account_id,
            now=10_001.0,
            target_chat_id=primary_group_id,
        )

        self.assertEqual([(primary_group_id, 0)], routes)

    def test_explicit_source_group_is_not_reordered_by_other_group_activity(self):
        send_as_id = 301299112
        account_id = 7001
        primary_group_id = -1002083016447
        alternate_group_id = -1001680975844
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        state_module.set_game_group_route_config({
            "enabled": True,
            "primary_group_id": primary_group_id,
            "backup_group_ids": [alternate_group_id],
            "topic_id_by_group": {str(primary_group_id): 0, str(alternate_group_id): 7310786},
        })
        now = 10_000.0
        runtime.note_game_group_bot_activity(alternate_group_id, now=now)

        routes = runtime._game_group_route_candidates(
            send_as_id,
            account_id,
            now=now,
            target_chat_id=primary_group_id,
        )

        self.assertEqual([(primary_group_id, 0)], routes)

    async def test_primary_text_send_as_failure_fails_over_once_to_backup(self):
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
        client = _FakeClient([RuntimeError("You can't send messages as the specified peer"), "ok"])

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
        self.assertEqual(backup_group_id, int(client.sent_requests[-1].peer.id))

    async def test_explicit_reply_route_sends_only_to_source_group(self):
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
        client = _FakeClient(["ok"])

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
                ".助阵",
                send_as_id=send_as_id,
                priority="probe",
                track=False,
                reply_to=777,
                target_chat_id=backup_group_id,
            )

        self.assertEqual(910001, result.id)
        self.assertEqual(1, len(client.sent_requests))
        request = client.sent_requests[0]
        self.assertEqual(backup_group_id, int(request.peer.id))
        self.assertEqual(777, int(request.reply_to.reply_to_msg_id))
        self.assertEqual(7310786, int(request.reply_to.top_msg_id))

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
        guard_note_mock.assert_called_once_with(".观星台", send_as_id, 920002, sent_at=1234.5, chat_id=123456)
        note_sent_mock.assert_called_once_with(
            ".观星台",
            sent_at=1234.5,
            priority=runtime.SEND_PRIORITY_NORMAL,
            msg_id=920002,
        )
        observer_mock.assert_called_once()
        audit_mock.assert_awaited()
        pending = state_module.get_identity_state(send_as_id)["pending_tasks"][(123456, 920002)]
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
