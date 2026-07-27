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


class RealmAvailabilityTests(unittest.TestCase):
    """境界表覆盖与「未知境界」的宽容语义。

    2026-07-26 WA 突破到「半步炼虚」，该名字不在 REALM_SORT_ORDER 里，
    is_small_world_realm_available 把未知境界当 False，enforce 立刻关掉了
    这个高香火身份的小世界（探寻裂缝也一并被关）。突破出新段位的身份此前
    必然已过门槛，未知境界应按放行处理。
    """

    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        self.identity_id = 990201
        state_module.ensure_identity_registered(self.identity_id)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_lianxu_realms_are_ranked_above_huashen(self):
        base = state_module.REALM_SORT_INDEX["化神后期大圆满"]
        for realm in ("半步炼虚", "炼虚初期", "炼虚中期", "炼虚后期", "炼虚后期大圆满"):
            with self.subTest(realm=realm):
                self.assertIn(realm, state_module.REALM_SORT_INDEX)
                self.assertGreater(state_module.REALM_SORT_INDEX[realm], base)

    def test_small_world_stays_available_after_breakthrough_to_banbu_lianxu(self):
        state_module.update_send_as_profile(self.identity_id, realm="半步炼虚")

        self.assertTrue(state_module.is_small_world_realm_available(self.identity_id))
        self.assertTrue(state_module.is_yuanying_realm_available(self.identity_id))
        self.assertTrue(state_module.is_explore_rift_realm_available(self.identity_id))

    def test_unknown_future_realm_does_not_disable_small_world(self):
        """下一个没进表的新境界（如「合体初期」）不应重蹈覆辙。"""
        state_module.update_send_as_profile(self.identity_id, realm="合体初期")

        self.assertTrue(state_module.is_small_world_realm_available(self.identity_id))

    def test_low_realm_is_still_gated(self):
        state_module.update_send_as_profile(self.identity_id, realm="结丹后期")

        self.assertFalse(state_module.is_small_world_realm_available(self.identity_id))

    def test_empty_realm_stays_conservative(self):
        state_module.update_send_as_profile(self.identity_id, realm="")

        self.assertFalse(state_module.is_small_world_realm_available(self.identity_id))


if __name__ == "__main__":
    unittest.main()
