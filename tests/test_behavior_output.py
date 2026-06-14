import unittest

from model.behavior_output import (
    BehaviorOutput,
    ControlSignal,
    apply_control_policy,
    empty,
    merge,
    with_deferred,
    with_notify,
    with_send,
)


class BehaviorOutputTests(unittest.TestCase):
    def test_empty_output_is_noop(self):
        output = empty()

        self.assertEqual((), output.sends)
        self.assertEqual((), output.notifies)
        self.assertEqual((), output.actions)
        self.assertEqual((), output.deferred_actions)
        self.assertEqual(ControlSignal.NONE, output.control)
        self.assertFalse(output.should_short_circuit)
        self.assertTrue(output.is_noop())
        self.assertEqual(output, apply_control_policy(output))

    def test_helpers_build_single_item_outputs(self):
        self.assertEqual(("send-1",), with_send("send-1").sends)
        self.assertEqual(("notify-1",), with_notify("notify-1").notifies)
        self.assertEqual(("deferred-1",), with_deferred("deferred-1").deferred_actions)

        output = (
            BehaviorOutput.empty()
            .with_send("send-1")
            .with_notify("notify-1")
            .with_action("action-1")
            .with_deferred("deferred-1")
        )

        self.assertEqual(("send-1",), output.sends)
        self.assertEqual(("notify-1",), output.notifies)
        self.assertEqual(("action-1",), output.actions)
        self.assertEqual(("deferred-1",), output.deferred_actions)
        self.assertFalse(output.is_noop())

    def test_disable_self_clears_sends_and_actions_but_retains_side_channels(self):
        output = BehaviorOutput(
            sends=("send-1",),
            notifies=("notify-1",),
            actions=("action-1",),
            deferred_actions=("deferred-1",),
            control=ControlSignal.DISABLE_SELF,
        )

        effective = apply_control_policy(output)

        self.assertTrue(effective.should_short_circuit)
        self.assertEqual((), effective.sends)
        self.assertEqual((), effective.actions)
        self.assertEqual(("notify-1",), effective.notifies)
        self.assertEqual(("deferred-1",), effective.deferred_actions)
        self.assertEqual(ControlSignal.DISABLE_SELF, effective.control)

    def test_pause_global_does_not_drop_notifies_or_deferred_actions(self):
        output = BehaviorOutput(
            sends=("send-1",),
            notifies=("notify-1",),
            actions=("action-1",),
            deferred_actions=("deferred-1",),
            control=ControlSignal.PAUSE_GLOBAL,
        )

        effective = output.effective_after_control()

        self.assertTrue(effective.should_short_circuit)
        self.assertEqual(("send-1",), effective.sends)
        self.assertEqual(("action-1",), effective.actions)
        self.assertEqual(("notify-1",), effective.notifies)
        self.assertEqual(("deferred-1",), effective.deferred_actions)

    def test_stop_chain_does_not_drop_notifies_or_deferred_actions(self):
        output = BehaviorOutput(
            notifies=("notify-1",),
            deferred_actions=("deferred-1",),
            control=ControlSignal.STOP_CHAIN,
        )

        effective = apply_control_policy(output)

        self.assertTrue(effective.should_short_circuit)
        self.assertEqual(("notify-1",), effective.notifies)
        self.assertEqual(("deferred-1",), effective.deferred_actions)
        self.assertEqual(ControlSignal.STOP_CHAIN, effective.control)

    def test_merge_keeps_stable_order_and_strongest_control(self):
        first = BehaviorOutput(
            sends=("send-1",),
            notifies=("notify-1",),
            actions=("action-1",),
            deferred_actions=("deferred-1",),
            control=ControlSignal.DISABLE_SELF,
        )
        second = BehaviorOutput(
            sends=("send-2",),
            notifies=("notify-2",),
            actions=("action-2",),
            deferred_actions=("deferred-2",),
            control=ControlSignal.STOP_CHAIN,
        )

        output = merge([first, None, second])

        self.assertEqual(("send-1", "send-2"), output.sends)
        self.assertEqual(("notify-1", "notify-2"), output.notifies)
        self.assertEqual(("action-1", "action-2"), output.actions)
        self.assertEqual(("deferred-1", "deferred-2"), output.deferred_actions)
        self.assertEqual(ControlSignal.STOP_CHAIN, output.control)

    def test_invalid_control_is_rejected(self):
        with self.assertRaises(ValueError):
            BehaviorOutput(control="bad")


if __name__ == "__main__":
    unittest.main()
