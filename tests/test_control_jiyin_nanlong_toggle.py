import asyncio
import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import control
from model import state as state_module
from model.features import jiyin, nanlong


class ControlJiyinNanlongToggleTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def _prepare_identity(self, identity_id):
        state_module.ensure_identity_registered(identity_id)
        return identity_id

    def _seed_jiyin_pending(self, next_time):
        state_module.state["jiyin_enabled"] = False
        state_module.state["next_jiyin_time"] = next_time
        state_module.state["jiyin_reply_to_msg_id"] = 22027
        state_module.state["jiyin_last_error"] = "旧极阴错误"

    def _seed_nanlong_pending(self, next_time):
        state_module.state["nanlong_enabled"] = False
        state_module.state["next_nanlong_time"] = next_time
        state_module.state["nanlong_reply_to_msg_id"] = 22028
        state_module.state["nanlong_reply_due_at"] = 1_700_000_120.0
        state_module.state["nanlong_last_msg_id"] = 22029
        state_module.state["nanlong_retry_count"] = 2
        state_module.state["nanlong_last_command"] = ".交换法宝"
        state_module.state["nanlong_last_error"] = "旧南陇侯错误"

    def _snapshot_jiyin(self):
        return {
            "next_jiyin_time": state_module.state["next_jiyin_time"],
            "jiyin_reply_to_msg_id": state_module.state["jiyin_reply_to_msg_id"],
            "jiyin_last_error": state_module.state["jiyin_last_error"],
        }

    def _snapshot_nanlong(self):
        return {
            "next_nanlong_time": state_module.state["next_nanlong_time"],
            "nanlong_reply_to_msg_id": state_module.state["nanlong_reply_to_msg_id"],
            "nanlong_reply_due_at": state_module.state["nanlong_reply_due_at"],
            "nanlong_last_msg_id": state_module.state["nanlong_last_msg_id"],
            "nanlong_retry_count": state_module.state["nanlong_retry_count"],
            "nanlong_last_command": state_module.state["nanlong_last_command"],
            "nanlong_last_error": state_module.state["nanlong_last_error"],
        }

    def _set_module_enabled(self, module_name, identity_id, now=1_700_000_000.0):
        with (
            patch.object(control.time, "time", return_value=now),
            patch.object(control, "save_state") as save_mock,
            patch.object(control, "console_log") as log_mock,
            patch.object(jiyin, "mark_dirty") as jiyin_dirty_mock,
            patch.object(nanlong, "mark_dirty") as nanlong_dirty_mock,
        ):
            ok, message = asyncio.run(control.set_module_enabled(module_name, True, send_as_id=identity_id))
        return ok, message, save_mock, log_mock, jiyin_dirty_mock, nanlong_dirty_mock

    def _assert_dirty_timer_logged(self, log_mock, module_name, timer_key):
        messages = [args[0] for args, _kwargs in log_mock.call_args_list if args]
        self.assertTrue(
            any(module_name in message and timer_key in message and "异常计时" in message for message in messages),
            messages,
        )

    def test_manual_enable_dirty_jiyin_next_time_keeps_pending_fail_closed(self):
        dirty_values = ("nan", "inf", "-inf", "not-a-timestamp")
        identity_id = self._prepare_identity(990501)

        for dirty_value in dirty_values:
            with self.subTest(dirty_value=dirty_value):
                with state_module.use_identity(identity_id):
                    self._seed_jiyin_pending(dirty_value)
                    before = self._snapshot_jiyin()

                ok, message, save_mock, log_mock, jiyin_dirty_mock, _nanlong_dirty_mock = self._set_module_enabled(
                    "极阴祖师",
                    identity_id,
                )

                self.assertTrue(ok, message)
                save_mock.assert_called_once()
                jiyin_dirty_mock.assert_not_called()
                self._assert_dirty_timer_logged(log_mock, "极阴祖师", "next_jiyin_time")
                with state_module.use_identity(identity_id):
                    self.assertTrue(state_module.state["jiyin_enabled"])
                    self.assertEqual(before, self._snapshot_jiyin())

    def test_manual_enable_dirty_nanlong_next_time_keeps_pending_fail_closed(self):
        dirty_values = ("nan", "inf", "-inf", "not-a-timestamp")
        identity_id = self._prepare_identity(990502)

        for dirty_value in dirty_values:
            with self.subTest(dirty_value=dirty_value):
                with state_module.use_identity(identity_id):
                    self._seed_nanlong_pending(dirty_value)
                    before = self._snapshot_nanlong()

                ok, message, save_mock, log_mock, _jiyin_dirty_mock, nanlong_dirty_mock = self._set_module_enabled(
                    "南陇侯",
                    identity_id,
                )

                self.assertTrue(ok, message)
                save_mock.assert_called_once()
                nanlong_dirty_mock.assert_not_called()
                self._assert_dirty_timer_logged(log_mock, "南陇侯", "next_nanlong_time")
                with state_module.use_identity(identity_id):
                    self.assertTrue(state_module.state["nanlong_enabled"])
                    self.assertEqual(before, self._snapshot_nanlong())

    def test_manual_enable_clean_jiyin_due_time_keeps_clear_semantics(self):
        now = 1_700_000_000.0
        identity_id = self._prepare_identity(990503)

        for next_time in (0, now - 1):
            with self.subTest(next_time=next_time):
                with state_module.use_identity(identity_id):
                    self._seed_jiyin_pending(next_time)

                ok, message, _save_mock, log_mock, jiyin_dirty_mock, _nanlong_dirty_mock = self._set_module_enabled(
                    "极阴祖师",
                    identity_id,
                )

                self.assertTrue(ok, message)
                jiyin_dirty_mock.assert_called_once()
                messages = [args[0] for args, _kwargs in log_mock.call_args_list if args]
                self.assertFalse(any("异常计时" in message for message in messages), messages)
                with state_module.use_identity(identity_id):
                    self.assertTrue(state_module.state["jiyin_enabled"])
                    self.assertEqual(0, state_module.state["next_jiyin_time"])
                    self.assertEqual(0, state_module.state["jiyin_reply_to_msg_id"])
                    self.assertEqual("", state_module.state["jiyin_last_error"])

    def test_manual_enable_clean_nanlong_due_time_keeps_clear_semantics(self):
        now = 1_700_000_000.0
        identity_id = self._prepare_identity(990504)

        for next_time in (0, now - 1):
            with self.subTest(next_time=next_time):
                with state_module.use_identity(identity_id):
                    self._seed_nanlong_pending(next_time)

                ok, message, _save_mock, log_mock, _jiyin_dirty_mock, nanlong_dirty_mock = self._set_module_enabled(
                    "南陇侯",
                    identity_id,
                )

                self.assertTrue(ok, message)
                nanlong_dirty_mock.assert_called_once()
                messages = [args[0] for args, _kwargs in log_mock.call_args_list if args]
                self.assertFalse(any("异常计时" in message for message in messages), messages)
                with state_module.use_identity(identity_id):
                    self.assertTrue(state_module.state["nanlong_enabled"])
                    self.assertEqual(0, state_module.state["next_nanlong_time"])
                    self.assertEqual(0, state_module.state["nanlong_reply_to_msg_id"])
                    self.assertEqual(0, state_module.state["nanlong_reply_due_at"])
                    self.assertEqual(0, state_module.state["nanlong_last_msg_id"])
                    self.assertEqual(0, state_module.state["nanlong_retry_count"])
                    self.assertEqual("", state_module.state["nanlong_last_command"])
                    self.assertEqual("", state_module.state["nanlong_last_error"])

    def test_manual_enable_clean_future_time_keeps_pending_semantics(self):
        now = 1_700_000_000.0
        cases = (
            ("极阴祖师", 990505, self._seed_jiyin_pending, self._snapshot_jiyin, "jiyin_enabled"),
            ("南陇侯", 990506, self._seed_nanlong_pending, self._snapshot_nanlong, "nanlong_enabled"),
        )

        for module_name, identity_id, seed_pending, snapshot_pending, enabled_key in cases:
            with self.subTest(module_name=module_name):
                self._prepare_identity(identity_id)
                with state_module.use_identity(identity_id):
                    seed_pending(now + 600)
                    before = snapshot_pending()

                ok, message, _save_mock, log_mock, jiyin_dirty_mock, nanlong_dirty_mock = self._set_module_enabled(
                    module_name,
                    identity_id,
                )

                self.assertTrue(ok, message)
                jiyin_dirty_mock.assert_not_called()
                nanlong_dirty_mock.assert_not_called()
                messages = [args[0] for args, _kwargs in log_mock.call_args_list if args]
                self.assertFalse(any("异常计时" in message for message in messages), messages)
                with state_module.use_identity(identity_id):
                    self.assertTrue(state_module.state[enabled_key])
                    self.assertEqual(before, snapshot_pending())


if __name__ == "__main__":
    unittest.main()
