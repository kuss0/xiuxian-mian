import copy
import unittest

from model import state as state_module


class StateHygieneTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_pet_warm_name_is_trimmed(self):
        identity_id = 990101
        state_module.ensure_identity_registered(identity_id)

        state_module.update_send_as_profile(identity_id, pet_warm_name="  warm blade  ")

        self.assertEqual("warm blade", state_module.get_send_as_profile(identity_id)["pet_warm_name"])
        self.assertEqual("warm blade", state_module.get_pet_warm_name(identity_id))

    def test_remove_identity_cleans_identity_owned_meta_records(self):
        removed_id = 990102
        retained_id = 990103
        state_module.ensure_identity_registered(removed_id)
        state_module.ensure_identity_registered(retained_id)
        state_module.set_tianjige_dao_path_records({
            str(removed_id): {"realm": "removed"},
            str(retained_id): {"realm": "retained"},
        })
        state_module.set_quiz_learning_watchers({
            "removed": {"identity_id": removed_id, "question": "old"},
            "retained": {"identity_id": retained_id, "question": "keep"},
        })

        self.assertTrue(state_module.remove_identity(removed_id))

        self.assertNotIn(str(removed_id), state_module.get_tianjige_dao_path_records())
        self.assertIn(str(retained_id), state_module.get_tianjige_dao_path_records())
        self.assertNotIn("removed", state_module.get_quiz_learning_watchers())
        self.assertIn("retained", state_module.get_quiz_learning_watchers())


if __name__ == "__main__":
    unittest.main()
