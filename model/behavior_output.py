from dataclasses import dataclass, replace


class ControlSignal:
    NONE = "none"
    DISABLE_SELF = "disable_self"
    PAUSE_GLOBAL = "pause_global"
    STOP_CHAIN = "stop_chain"


CONTROL_NONE = ControlSignal.NONE
CONTROL_DISABLE_SELF = ControlSignal.DISABLE_SELF
CONTROL_PAUSE_GLOBAL = ControlSignal.PAUSE_GLOBAL
CONTROL_STOP_CHAIN = ControlSignal.STOP_CHAIN

_VALID_CONTROLS = {
    ControlSignal.NONE,
    ControlSignal.DISABLE_SELF,
    ControlSignal.PAUSE_GLOBAL,
    ControlSignal.STOP_CHAIN,
}
_SHORT_CIRCUIT_CONTROLS = {
    ControlSignal.DISABLE_SELF,
    ControlSignal.PAUSE_GLOBAL,
    ControlSignal.STOP_CHAIN,
}


def _tuple_items(items):
    if items is None:
        return ()
    if isinstance(items, tuple):
        return items
    return tuple(items)


def _normalize_control(control):
    control = str(control or ControlSignal.NONE)
    if control not in _VALID_CONTROLS:
        raise ValueError(f"unknown control signal: {control}")
    return control


def _stronger_control(left, right):
    order = {
        ControlSignal.NONE: 0,
        ControlSignal.DISABLE_SELF: 1,
        ControlSignal.PAUSE_GLOBAL: 2,
        ControlSignal.STOP_CHAIN: 3,
    }
    return left if order[left] >= order[right] else right


@dataclass(frozen=True)
class BehaviorOutput:
    sends: tuple = ()
    notifies: tuple = ()
    actions: tuple = ()
    deferred_actions: tuple = ()
    control: str = ControlSignal.NONE

    def __post_init__(self):
        object.__setattr__(self, "sends", _tuple_items(self.sends))
        object.__setattr__(self, "notifies", _tuple_items(self.notifies))
        object.__setattr__(self, "actions", _tuple_items(self.actions))
        object.__setattr__(self, "deferred_actions", _tuple_items(self.deferred_actions))
        object.__setattr__(self, "control", _normalize_control(self.control))

    @classmethod
    def empty(cls):
        return cls()

    @property
    def should_short_circuit(self):
        return self.control in _SHORT_CIRCUIT_CONTROLS

    def is_noop(self):
        return (
            not self.sends
            and not self.notifies
            and not self.actions
            and not self.deferred_actions
            and self.control == ControlSignal.NONE
        )

    def with_send(self, item):
        return replace(self, sends=self.sends + (item,))

    def with_notify(self, item):
        return replace(self, notifies=self.notifies + (item,))

    def with_action(self, item):
        return replace(self, actions=self.actions + (item,))

    def with_deferred(self, item):
        return replace(self, deferred_actions=self.deferred_actions + (item,))

    def with_control(self, control):
        return replace(self, control=_normalize_control(control))

    def effective_after_control(self):
        if self.control == ControlSignal.DISABLE_SELF:
            return replace(self, sends=(), actions=())
        return self


def empty():
    return BehaviorOutput.empty()


def with_send(item):
    return empty().with_send(item)


def with_notify(item):
    return empty().with_notify(item)


def with_deferred(item):
    return empty().with_deferred(item)


def merge(outputs):
    result = empty()
    for output in outputs:
        if output is None:
            continue
        if not isinstance(output, BehaviorOutput):
            output = BehaviorOutput(
                sends=getattr(output, "sends", ()),
                notifies=getattr(output, "notifies", ()),
                actions=getattr(output, "actions", ()),
                deferred_actions=getattr(output, "deferred_actions", ()),
                control=getattr(output, "control", ControlSignal.NONE),
            )
        result = BehaviorOutput(
            sends=result.sends + output.sends,
            notifies=result.notifies + output.notifies,
            actions=result.actions + output.actions,
            deferred_actions=result.deferred_actions + output.deferred_actions,
            control=_stronger_control(result.control, output.control),
        )
    return result


def apply_control_policy(output):
    if output is None:
        output = empty()
    if not isinstance(output, BehaviorOutput):
        output = BehaviorOutput(
            sends=getattr(output, "sends", ()),
            notifies=getattr(output, "notifies", ()),
            actions=getattr(output, "actions", ()),
            deferred_actions=getattr(output, "deferred_actions", ()),
            control=getattr(output, "control", ControlSignal.NONE),
        )
    return output.effective_after_control()
