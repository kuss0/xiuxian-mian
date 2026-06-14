import unittest

from model.persisted_state import PersistedState, PersistedValue


class PersistedValueTests(unittest.TestCase):
    def test_initial_value_is_clean(self):
        state = PersistedValue({"count": 1})

        self.assertEqual({"count": 1}, state.value)
        self.assertEqual({"count": 1}, state.get())
        self.assertIsNone(state.snapshot_if_dirty())

    def test_set_same_value_does_not_dirty(self):
        state = PersistedValue({"count": 1})

        state.set({"count": 1})

        self.assertIsNone(state.snapshot_if_dirty())

    def test_set_changed_value_snapshots_and_clears_dirty(self):
        state = PersistedValue({"count": 1})

        state.set({"count": 2})

        self.assertEqual({"count": 2}, state.snapshot_if_dirty())
        self.assertIsNone(state.snapshot_if_dirty())

    def test_update_noop_does_not_dirty(self):
        state = PersistedValue({"items": ["a"]})

        state.update(lambda value: {"items": ["a"]})

        self.assertIsNone(state.snapshot_if_dirty())

    def test_update_returned_changed_value_dirties(self):
        state = PersistedValue({"items": ["a"]})

        state.update(lambda value: {"items": value["items"] + ["b"]})

        self.assertEqual({"items": ["a", "b"]}, state.snapshot_if_dirty())

    def test_update_in_place_changed_value_dirties(self):
        state = PersistedValue({"items": ["a"]})

        def append_item(value):
            value["items"].append("b")

        state.update(append_item)

        self.assertEqual({"items": ["a", "b"]}, state.snapshot_if_dirty())

    def test_restore_bad_payload_keeps_current_value_and_remains_clean(self):
        state = PersistedValue({"count": 1})
        state.set({"count": 2})

        state.restore(object())

        self.assertEqual({"count": 2}, state.get())
        self.assertIsNone(state.snapshot_if_dirty())

    def test_restore_good_payload_never_dirties(self):
        state = PersistedValue({"count": 1})

        state.restore({"count": 3})

        self.assertEqual({"count": 3}, state.get())
        self.assertIsNone(state.snapshot_if_dirty())

    def test_restore_none_payload_resets_to_default_and_stays_clean(self):
        state = PersistedValue({"count": 1})
        state.set({"count": 4})

        state.restore(None)

        self.assertEqual({"count": 1}, state.get())
        self.assertIsNone(state.snapshot_if_dirty())

    def test_alias_name_matches_value_facade(self):
        state = PersistedState({"enabled": True})

        state.set({"enabled": False})

        self.assertEqual({"enabled": False}, state.snapshot_if_dirty())


if __name__ == "__main__":
    unittest.main()
