import inspect
import unittest
from unittest.mock import Mock, patch

from model import app
from model import runtime
from model.features import _phaseful


class SentCommandObserverTests(unittest.TestCase):
    def test_observer_type_error_does_not_repeat_a_partial_side_effect(self):
        seen = []

        def observer(identity_id, command, **metadata):
            seen.append((identity_id, command, metadata))
            if len(seen) == 1:
                raise TypeError("failure after a state transition")

        with (
            patch.object(runtime, "_GAME_COMMAND_SENT_OBSERVERS", [observer]),
            patch.object(runtime.traceback, "print_exc") as error,
        ):
            runtime._notify_game_command_sent_observers(".test", 12, 100.0, 42, game_group_id=-123)
        self.assertEqual([(12, ".test", {"now": 100.0, "msg_id": 42, "game_group_id": -123})], seen)
        error.assert_called_once()

    def test_failing_observer_cannot_interrupt_the_next_observer(self):
        broken = Mock(side_effect=TypeError("observer defect"))
        following = Mock()
        with (
            patch.object(runtime, "_GAME_COMMAND_SENT_OBSERVERS", [broken, following]),
            patch.object(runtime.traceback, "print_exc"),
        ):
            runtime._notify_game_command_sent_observers(".test", 12, 100.0, 42, track=True)
        broken.assert_called_once()
        following.assert_called_once_with(12, ".test", now=100.0, msg_id=42, track=True)

    def test_production_observers_accept_the_metadata_contract(self):
        for observer in (app._observe_sent_for_early_reply_replay, _phaseful.observe_phaseful_identity_message):
            with self.subTest(observer=observer.__name__):
                inspect.signature(observer).bind(
                    12, ".test", now=100.0, msg_id=42, track=True, reply_to=0,
                    priority="normal", recovered=False, game_group_id=-123,
                    topic_id=0, source_module="audit", send_elapsed_sec=0.1,
                )
