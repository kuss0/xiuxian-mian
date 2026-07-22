import copy
import unittest
from unittest.mock import patch

from model import state as state_module
from model import ui


class UiSnapshotCacheTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._cache_snapshot = dict(ui._ui_state_get_cache)
        ui._ui_state_get_cache.clear()
        ui._ui_state_get_cache.update({"expires_at": 0.0, "snapshot": None})
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        ui._ui_state_get_cache.clear()
        ui._ui_state_get_cache.update(self._cache_snapshot)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_get_state_reuses_cache_but_fresh_snapshot_replaces_it(self):
        with patch.object(ui, "collect_identity_rows_for_duel_presets", return_value=[]) as collect_mock, \
                patch.object(ui, "get_identity_ids", return_value=[]), \
                patch.object(ui, "consume_unseen_startup_alerts", return_value=[]):
            first = ui.get_ui_snapshot("session-a", use_cache=True)
            second = ui.get_ui_snapshot("session-a", use_cache=True)
            fresh = ui.get_ui_snapshot("session-a")
            cached_fresh = ui.get_ui_snapshot("session-a", use_cache=True)

        self.assertEqual(first["generated_at"], second["generated_at"])
        self.assertEqual(fresh["generated_at"], cached_fresh["generated_at"])
        self.assertIsNot(first, fresh)
        self.assertEqual(2, collect_mock.call_count)

    def test_get_state_cache_is_shared_across_sessions(self):
        with patch.object(ui, "collect_identity_rows_for_duel_presets", return_value=[]) as collect_mock, \
                patch.object(ui, "get_identity_ids", return_value=[]), \
                patch.object(ui, "consume_unseen_startup_alerts", side_effect=lambda token, alerts: [token]):
            first = ui.get_ui_snapshot("session-a", use_cache=True)
            second = ui.get_ui_snapshot("session-b", use_cache=True)

        self.assertEqual(["session-a"], first["startup_alerts"])
        self.assertEqual(["session-b"], second["startup_alerts"])
        self.assertEqual(1, collect_mock.call_count)


if __name__ == "__main__":
    unittest.main()
