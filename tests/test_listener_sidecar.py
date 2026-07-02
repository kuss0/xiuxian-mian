import copy
import unittest

from model import listener_sidecar
from model import state as state_module


class ListenerSidecarTests(unittest.TestCase):
    def test_listener_account_ids_prefers_configured_listener_accounts(self):
        snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module.set_game_listener_account_ids([301299112, 7538826434])
            accounts = {
                "301299112": {},
                "7538826434": {},
                "8659059191": {},
            }

            self.assertEqual([301299112, 7538826434], listener_sidecar._listener_account_ids(accounts))
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(snapshot)

    def test_listener_account_ids_falls_back_to_all_accounts_without_config(self):
        snapshot = copy.deepcopy(state_module._meta_state)
        try:
            state_module.set_game_listener_account_ids([])
            state_module.set_replica_listener_account_map({})
            state_module.set_replica_dispatch_listener_account_map({})
            accounts = {
                "301299112": {},
                "8659059191": {},
            }

            self.assertEqual([301299112, 8659059191], listener_sidecar._listener_account_ids(accounts))
        finally:
            state_module._meta_state.clear()
            state_module._meta_state.update(snapshot)
