import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path

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

    def test_backup_sqlite_session_creates_independent_copy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "account_1.session"
            dst = Path(tmpdir) / "listener_account_1.session"
            with sqlite3.connect(src) as conn:
                conn.execute("create table sessions(dc_id integer primary key, server_address text)")
                conn.execute("insert into sessions values(1, 'example')")

            listener_sidecar._backup_sqlite_session(src, dst)

            with sqlite3.connect(dst) as conn:
                row = conn.execute("select server_address from sessions where dc_id=1").fetchone()
            self.assertEqual(("example",), row)

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
