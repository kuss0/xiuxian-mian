import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from model import delayed_actions


NON_FINITE_TIME_VALUES = (float("nan"), float("inf"), float("-inf"), "nan", "inf", "-inf")
BAD_TIME_VALUES = NON_FINITE_TIME_VALUES + ("not-a-time",)


class DelayedActionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        delayed_actions.reset_delayed_actions_for_tests()

    def tearDown(self):
        delayed_actions.reset_delayed_actions_for_tests()

    async def test_drain_due_actions_sends_in_due_then_id_order(self):
        sent = []

        async def fake_send(command, **kwargs):
            sent.append((command, kwargs))
            return SimpleNamespace(id=9000 + len(sent))

        delayed_actions.schedule_delayed_action(".第二", 20, send_as_id=2, now=1)
        delayed_actions.schedule_delayed_action(".第一", 10, send_as_id=1, now=1)
        delayed_actions.schedule_delayed_action(".第三", 20, send_as_id=3, now=1)

        results = await delayed_actions.drain_due_actions(20, fake_send)

        self.assertEqual([".第一", ".第二", ".第三"], [item[0] for item in sent])
        self.assertEqual(["sent", "sent", "sent"], [item["status"] for item in results])
        self.assertEqual([], delayed_actions.list_delayed_actions())

    async def test_drain_due_actions_passes_send_intent_metadata(self):
        calls = []

        async def fake_send(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(id=701)

        delayed_actions.schedule_delayed_action(
            ".测试",
            10,
            send_as_id=123,
            track=False,
            reply_to_msg_id=456,
            priority="retry",
            max_retry=2,
            reply_timeout=33,
            source_module="测试模块",
            op_id="op-1",
            chain_id="chain-1",
            delete_policy="auto_delete",
            now=1,
        )

        await delayed_actions.drain_due_actions(11, fake_send)

        self.assertEqual(1, len(calls))
        command, kwargs = calls[0]
        self.assertEqual(".测试", command)
        self.assertEqual(123, kwargs["send_as_id"])
        self.assertFalse(kwargs["track"])
        self.assertEqual(456, kwargs["reply_to"])
        self.assertEqual("retry", kwargs["priority"])
        self.assertEqual(2, kwargs["max_retry"])
        self.assertEqual(33, kwargs["reply_timeout"])
        self.assertEqual("测试模块", kwargs["source_module"])
        self.assertEqual("op-1", kwargs["op_id"])
        self.assertEqual("chain-1", kwargs["chain_id"])
        self.assertEqual("auto_delete", kwargs["delete_policy"])

    def test_schedule_with_dedupe_key_updates_existing_pending_action(self):
        first = delayed_actions.schedule_delayed_action(".旧", 10, dedupe_key="same", send_as_id=1, now=1)
        second = delayed_actions.schedule_delayed_action(".新", 30, dedupe_key="same", send_as_id=2, now=2)

        items = delayed_actions.list_delayed_actions()

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(1, len(items))
        self.assertEqual(".新", items[0]["command"])
        self.assertEqual(30, items[0]["due_at"])
        self.assertEqual(2, items[0]["send_as_id"])

    def test_schedule_with_dedupe_key_preserves_retry_attempts(self):
        first = delayed_actions.schedule_delayed_action(
            ".旧",
            10,
            dedupe_key="same",
            send_as_id=1,
            now=1,
            max_send_attempts=3,
        )
        delayed_actions._DELAYED_ACTIONS[first["id"]].attempts = 1

        second = delayed_actions.schedule_delayed_action(
            ".新",
            30,
            dedupe_key="same",
            send_as_id=2,
            now=2,
            max_send_attempts=3,
        )

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(1, second["attempts"])

    async def test_failed_send_reschedules_until_attempt_limit(self):
        async def fake_send(command, **kwargs):
            return None

        delayed_actions.schedule_delayed_action(
            ".会失败",
            10,
            send_as_id=1,
            now=1,
            max_send_attempts=2,
            retry_delay_sec=60,
        )

        first = await delayed_actions.drain_due_actions(10, fake_send)
        self.assertEqual(1, len(first))
        self.assertEqual("rescheduled", first[0]["status"])
        self.assertEqual(70.0, first[0]["due_at"])
        self.assertEqual(1, first[0]["attempts"])
        self.assertEqual(".会失败", first[0]["command"])
        self.assertEqual(1, first[0]["send_as_id"])
        pending = delayed_actions.list_delayed_actions()
        self.assertEqual("pending", pending[0]["status"])
        self.assertEqual(70, pending[0]["due_at"])

        second = await delayed_actions.drain_due_actions(70, fake_send)
        self.assertEqual(1, len(second))
        self.assertEqual(1, second[0]["id"])
        self.assertEqual("failed", second[0]["status"])
        self.assertEqual(2, second[0]["attempts"])
        self.assertEqual(".会失败", second[0]["command"])
        self.assertEqual([], delayed_actions.list_delayed_actions())
        self.assertEqual([], delayed_actions.list_delayed_actions(include_non_pending=True))

    async def test_missing_send_identity_fails_closed_without_sending(self):
        calls = []

        async def fake_send(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(id=1)

        delayed_actions.schedule_delayed_action(".缺身份", 10, now=1)

        result = await delayed_actions.drain_due_actions(10, fake_send)

        self.assertEqual([], calls)
        self.assertEqual(1, len(result))
        self.assertEqual("failed", result[0]["status"])
        self.assertEqual(0, result[0]["attempts"])
        self.assertEqual("missing send_as_id", result[0]["reason"])
        self.assertEqual(".缺身份", result[0]["command"])
        self.assertEqual(0, result[0]["send_as_id"])
        self.assertEqual([], delayed_actions.list_delayed_actions())

    def test_schedule_rejects_bad_timing_values_without_pending(self):
        for field_name in ("due_at", "now", "retry_delay_sec", "reply_timeout"):
            for bad_value in BAD_TIME_VALUES:
                with self.subTest(field_name=field_name, bad_value=repr(bad_value)):
                    delayed_actions.reset_delayed_actions_for_tests()
                    due_at = 10
                    kwargs = {"send_as_id": 1, "now": 1}
                    if field_name == "due_at":
                        due_at = bad_value
                    elif field_name == "now":
                        kwargs["now"] = bad_value
                    else:
                        kwargs[field_name] = bad_value

                    with self.assertRaisesRegex(ValueError, field_name):
                        delayed_actions.schedule_delayed_action(".坏时间", due_at, **kwargs)

                    self.assertEqual([], delayed_actions.list_delayed_actions(include_non_pending=True))

    def test_restore_skips_actions_with_bad_timing_values(self):
        for field_name in ("due_at", "retry_delay_sec", "created_at", "updated_at", "reply_timeout"):
            for bad_value in BAD_TIME_VALUES:
                with self.subTest(field_name=field_name, bad_value=repr(bad_value)):
                    delayed_actions.reset_delayed_actions_for_tests()
                    bad_action = {
                        "id": 1,
                        "command": ".坏时间",
                        "due_at": 10,
                        "send_as_id": 11,
                        "retry_delay_sec": 60,
                        "created_at": 1,
                        "updated_at": 1,
                    }
                    bad_action[field_name] = bad_value

                    delayed_actions.restore_delayed_actions(
                        {
                            "next_id": 2,
                            "actions": [
                                bad_action,
                                {"id": 2, "command": ".有效", "due_at": 20, "send_as_id": 22},
                            ],
                        }
                    )

                    pending = delayed_actions.list_delayed_actions()
                    all_items = delayed_actions.list_delayed_actions(include_non_pending=True)
                    self.assertEqual([2], [item["id"] for item in pending])
                    self.assertEqual([2], [item["id"] for item in all_items])

    async def test_drain_marks_existing_bad_due_at_failed_without_sending(self):
        async def fake_send(command, **kwargs):
            calls.append((command, kwargs))
            return SimpleNamespace(id=1)

        for bad_value in NON_FINITE_TIME_VALUES:
            with self.subTest(bad_value=repr(bad_value)):
                delayed_actions.reset_delayed_actions_for_tests()
                calls = []
                scheduled = delayed_actions.schedule_delayed_action(".坏时间", 10, send_as_id=1, now=1)
                delayed_actions._DELAYED_ACTIONS[scheduled["id"]].due_at = bad_value

                result = await delayed_actions.drain_due_actions(20, fake_send)

                self.assertEqual([], calls)
                self.assertEqual(1, len(result))
                self.assertEqual(scheduled["id"], result[0]["id"])
                self.assertEqual("failed", result[0]["status"])
                self.assertEqual(0, result[0]["attempts"])
                self.assertEqual("due_at must be finite", result[0]["reason"])
                self.assertEqual(".坏时间", result[0]["command"])
                self.assertEqual([], delayed_actions.list_delayed_actions())
                self.assertEqual([], delayed_actions.list_delayed_actions(include_non_pending=True))

    def test_cancel_by_id_or_dedupe_key(self):
        first = delayed_actions.schedule_delayed_action(".a", 10, dedupe_key="a", now=1)
        delayed_actions.schedule_delayed_action(".b", 20, dedupe_key="b", now=1)

        with patch.object(delayed_actions, "_mark_dirty") as mark_dirty:
            self.assertTrue(delayed_actions.cancel_delayed_action(first["id"]))
            self.assertTrue(delayed_actions.cancel_delayed_action(dedupe_key="b"))
            self.assertFalse(delayed_actions.cancel_delayed_action(dedupe_key="missing"))

        self.assertEqual(2, mark_dirty.call_count)
        self.assertEqual([], delayed_actions.list_delayed_actions())

    def test_snapshot_restore_round_trip_preserves_shape_and_next_id(self):
        first = delayed_actions.schedule_delayed_action(
            ".第一",
            10,
            send_as_id=11,
            priority="retry",
            max_retry=2,
            reply_timeout=33,
            source_module="模块",
            op_id="op-1",
            chain_id="chain-1",
            delete_policy="auto_delete",
            dedupe_key="same",
            now=1,
            extra={"kind": "check"},
        )
        delayed_actions.schedule_delayed_action(".第二", 20, send_as_id=22, now=2)

        snapshot = delayed_actions.snapshot_delayed_actions()
        delayed_actions.reset_delayed_actions_for_tests()

        restored = delayed_actions.restore_delayed_actions(snapshot)
        items = delayed_actions.list_delayed_actions()
        next_item = delayed_actions.schedule_delayed_action(".第三", 30, send_as_id=33, now=3)

        self.assertEqual(snapshot, restored)
        self.assertEqual(2, len(items))
        self.assertEqual(first["id"], items[0]["id"])
        self.assertEqual(".第一", items[0]["command"])
        self.assertEqual({"kind": "check"}, items[0]["extra"])
        self.assertTrue(items[0]["track"])
        self.assertEqual(0, items[0]["reply_to_msg_id"])
        self.assertGreater(next_item["id"], snapshot["next_id"])

    def test_state_adapter_round_trips_and_fails_closed_on_bad_payload(self):
        delayed_actions.schedule_delayed_action(".持久化", 10, send_as_id=11, now=1)
        state_dict = {}

        exported = delayed_actions.export_to_state(state_dict)
        delayed_actions.reset_delayed_actions_for_tests()
        restored = delayed_actions.restore_from_state(state_dict)

        self.assertEqual(exported, restored)
        self.assertEqual([".持久化"], [item["command"] for item in delayed_actions.list_delayed_actions()])

        delayed_actions.restore_from_state({delayed_actions.DELAYED_ACTIONS_STATE_KEY: ["bad"]})
        self.assertEqual([], delayed_actions.list_delayed_actions(include_non_pending=True))

    def test_restore_delayed_actions_skips_malformed_and_fails_invalid_identity(self):
        restored = delayed_actions.restore_delayed_actions(
            {
                "next_id": "9",
                "actions": [
                    "bad",
                    {"id": "nope", "command": ".坏", "due_at": 10, "send_as_id": 1},
                    {"id": 2, "command": "   ", "due_at": 10, "send_as_id": 1},
                    {"id": 3, "command": ".缺身份", "due_at": 10},
                    {"id": 4, "command": ".有效", "due_at": 20, "send_as_id": 44},
                ],
            }
        )

        pending = delayed_actions.list_delayed_actions()
        all_items = delayed_actions.list_delayed_actions(include_non_pending=True)
        next_item = delayed_actions.schedule_delayed_action(".下一条", 30, send_as_id=55, now=1)

        self.assertEqual([4], [item["id"] for item in pending])
        self.assertEqual([4], [item["id"] for item in all_items])
        self.assertEqual(9, restored["next_id"])
        self.assertEqual(10, next_item["id"])

    def test_restore_delayed_actions_drops_legacy_terminal_rows(self):
        restored = delayed_actions.restore_delayed_actions(
            {
                "next_id": 7,
                "actions": [
                    {
                        "id": 6,
                        "command": ".旧失败",
                        "due_at": 10,
                        "send_as_id": 66,
                        "status": "failed",
                        "last_error": "send returned none",
                    },
                    {
                        "id": 7,
                        "command": ".仍待发送",
                        "due_at": 20,
                        "send_as_id": 77,
                        "status": "pending",
                    },
                ],
            }
        )

        self.assertEqual([7], [item["id"] for item in restored["actions"]])
        self.assertEqual([".仍待发送"], [item["command"] for item in delayed_actions.list_delayed_actions()])

    async def test_due_drain_after_restore(self):
        delayed_actions.restore_delayed_actions(
            {
                "next_id": 5,
                "actions": [
                    {"id": 5, "command": ".恢复后发送", "due_at": 10, "send_as_id": 66},
                ],
            }
        )
        sent = []

        async def fake_send(command, **kwargs):
            sent.append((command, kwargs))
            return SimpleNamespace(id=8801)

        results = await delayed_actions.drain_due_actions(10, fake_send)

        self.assertEqual([(".恢复后发送", {"send_as_id": 66, "track": True})], sent)
        self.assertEqual(1, len(results))
        self.assertEqual(5, results[0]["id"])
        self.assertEqual("sent", results[0]["status"])
        self.assertEqual(8801, results[0]["message_id"])
        self.assertEqual(66, results[0]["send_as_id"])
        self.assertEqual([], delayed_actions.list_delayed_actions())


class DelayedActionsPersistenceTests(unittest.TestCase):
    def setUp(self):
        try:
            from model import persistence
            from model import state as state_module
        except ModuleNotFoundError as exc:
            if exc.name == "telethon":
                raise unittest.SkipTest("telethon is not installed in this test environment") from exc
            raise
        self.persistence = persistence
        self.state_module = state_module
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._db_conn_snapshot = persistence._db_conn
        self._db_initialized_snapshot = persistence._db_initialized
        self._state_dirty_snapshot = persistence._state_dirty
        persistence._db_conn = None
        persistence._db_initialized = False
        persistence._state_dirty = False
        delayed_actions.reset_delayed_actions_for_tests()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        delayed_actions.reset_delayed_actions_for_tests()
        persistence = getattr(self, "persistence", None)
        state_module = getattr(self, "state_module", None)
        if persistence is not None:
            if persistence._db_conn is not None:
                persistence._db_conn.close()
            persistence._db_conn = self._db_conn_snapshot
            persistence._db_initialized = self._db_initialized_snapshot
            persistence._state_dirty = self._state_dirty_snapshot
        if state_module is not None:
            state_module._meta_state.clear()
            state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _reset_persistence_connection(self):
        persistence = self.persistence
        if persistence._db_conn is not None:
            persistence._db_conn.close()
        persistence._db_conn = None
        persistence._db_initialized = False

    def test_save_load_state_round_trips_delayed_actions_meta_bucket(self):
        persistence = self.persistence
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                delayed_actions.schedule_delayed_action(".保存后恢复", 42, send_as_id=77, now=1)

                self.assertTrue(persistence.save_state())
                conn = persistence.get_db_conn()
                row = conn.execute(
                    "SELECT value FROM meta WHERE key = ?",
                    (delayed_actions.DELAYED_ACTIONS_STATE_KEY,),
                ).fetchone()
                self.assertIsNotNone(row)

                delayed_actions.reset_delayed_actions_for_tests()
                self._reset_persistence_connection()
                self.assertTrue(persistence.load_state())

        self.assertEqual([".保存后恢复"], [item["command"] for item in delayed_actions.list_delayed_actions()])
        self.assertEqual(77, delayed_actions.list_delayed_actions()[0]["send_as_id"])

    def test_load_state_bad_delayed_actions_payload_fails_closed(self):
        persistence = self.persistence
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                persistence.init_db()
                conn = persistence.get_db_conn()
                conn.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                    (delayed_actions.DELAYED_ACTIONS_STATE_KEY, '["bad"]'),
                )
                conn.commit()
                delayed_actions.schedule_delayed_action(".不应残留", 10, send_as_id=1, now=1)
                self._reset_persistence_connection()

                self.assertTrue(persistence.load_state())

        self.assertEqual([], delayed_actions.list_delayed_actions(include_non_pending=True))

    def test_successful_drain_marks_dirty_and_save_persists_pop(self):
        persistence = self.persistence

        async def fake_send(command, **kwargs):
            return SimpleNamespace(id=901)

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                delayed_actions.schedule_delayed_action(".会发送", 10, send_as_id=77, now=1)
                self.assertTrue(persistence.save_state())
                self.assertFalse(persistence._state_dirty)

                result = asyncio.run(delayed_actions.drain_due_actions(10, fake_send))

                self.assertEqual(1, len(result))
                self.assertEqual("sent", result[0]["status"])
                self.assertEqual(901, result[0]["message_id"])
                self.assertEqual(".会发送", result[0]["command"])
                self.assertTrue(persistence._state_dirty)
                self.assertTrue(persistence.save_state())

                delayed_actions.reset_delayed_actions_for_tests()
                self._reset_persistence_connection()
                self.assertTrue(persistence.load_state())

        self.assertEqual([], delayed_actions.list_delayed_actions(include_non_pending=True))

    def test_failed_drain_reschedule_marks_dirty_and_save_persists_retry(self):
        persistence = self.persistence

        async def fake_send(command, **kwargs):
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "state.db")
            with patch.object(persistence, "DB_FILE", db_path):
                delayed_actions.schedule_delayed_action(
                    ".会重排",
                    10,
                    send_as_id=77,
                    now=1,
                    max_send_attempts=2,
                    retry_delay_sec=60,
                )
                self.assertTrue(persistence.save_state())
                self.assertFalse(persistence._state_dirty)

                result = asyncio.run(delayed_actions.drain_due_actions(10, fake_send))

                self.assertEqual(1, len(result))
                self.assertEqual("rescheduled", result[0]["status"])
                self.assertEqual(70.0, result[0]["due_at"])
                self.assertEqual(1, result[0]["attempts"])
                self.assertEqual(".会重排", result[0]["command"])
                self.assertTrue(persistence._state_dirty)
                self.assertTrue(persistence.save_state())

                delayed_actions.reset_delayed_actions_for_tests()
                self._reset_persistence_connection()
                self.assertTrue(persistence.load_state())

        pending = delayed_actions.list_delayed_actions()
        self.assertEqual(1, len(pending))
        self.assertEqual(".会重排", pending[0]["command"])
        self.assertEqual(70, pending[0]["due_at"])
        self.assertEqual(1, pending[0]["attempts"])


if __name__ == "__main__":
    unittest.main()
