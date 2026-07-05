import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def _write_session(self, path, auth_key):
        with sqlite3.connect(path) as conn:
            conn.execute("create table sessions(dc_id integer primary key, server_address text, auth_key blob)")
            conn.execute("insert into sessions values(1, 'example', ?)", (auth_key,))

    def test_listener_session_requires_independent_login(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "account_1.session"
            self._write_session(src, b"source-auth")

            with patch.object(listener_sidecar, "SESSION_DIR", tmpdir):
                with self.assertRaisesRegex(RuntimeError, "未独立授权"):
                    listener_sidecar._ensure_listener_session(1)

            self.assertFalse((Path(tmpdir) / "listener_account_1.session").exists())

    def test_listener_session_rejects_copied_auth_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "account_1.session"
            dst = Path(tmpdir) / "listener_account_1.session"
            self._write_session(src, b"same-auth")
            self._write_session(dst, b"same-auth")

            with patch.object(listener_sidecar, "SESSION_DIR", tmpdir):
                with self.assertRaisesRegex(RuntimeError, "auth_key 相同"):
                    listener_sidecar._ensure_listener_session(1)

    def test_listener_session_allows_distinct_auth_key(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "account_1.session"
            dst = Path(tmpdir) / "listener_account_1.session"
            self._write_session(src, b"source-auth")
            self._write_session(dst, b"listener-auth")

            with patch.object(listener_sidecar, "SESSION_DIR", tmpdir):
                self.assertEqual(str(Path(tmpdir) / "listener_account_1"), listener_sidecar._ensure_listener_session(1))

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
