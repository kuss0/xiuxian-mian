import copy
import json
from collections.abc import Callable
from typing import Generic, TypeVar


T = TypeVar("T")


class PersistedValue(Generic[T]):
    def __init__(self, default: T):
        self._default = self._serializable_copy(default)
        self._value = self._serializable_copy(default)
        self._dirty = False

    @property
    def value(self) -> T:
        return self._value

    def get(self) -> T:
        return self._value

    def set(self, value: T) -> None:
        next_value = self._serializable_copy(value)
        if next_value != self._value:
            self._value = next_value
            self._dirty = True

    def update(self, mutator: Callable[[T], T | None]) -> None:
        before = copy.deepcopy(self._value)
        working = copy.deepcopy(self._value)
        result = mutator(working)
        next_value = working if result is None else result
        next_value = self._serializable_copy(next_value)
        if next_value != before:
            self._value = next_value
            self._dirty = True

    def restore(self, payload: object) -> None:
        try:
            next_value = self._default if payload is None else self._serializable_copy(payload)
        except (TypeError, ValueError):
            next_value = self._value
        self._value = next_value
        self._dirty = False

    def snapshot_if_dirty(self) -> T | None:
        if not self._dirty:
            return None
        snapshot = self._serializable_copy(self._value)
        self._dirty = False
        return snapshot

    @staticmethod
    def _serializable_copy(value):
        return json.loads(json.dumps(copy.deepcopy(value), ensure_ascii=False))


PersistedState = PersistedValue
