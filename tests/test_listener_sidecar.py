import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

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

    async def _connect_saved_accounts_with(self, *, accounts, connected=False):
        heartbeats = []

        async def fake_connect(_account_id, _acct_info):
            return connected

        with (
            patch.object(listener_sidecar, "load_state", return_value=True),
            patch.object(listener_sidecar, "has_persisted_identity_rows", return_value=True),
            patch.object(listener_sidecar, "get_accounts", return_value=accounts),
            patch.object(listener_sidecar, "_connect_listener_account", new=AsyncMock(side_effect=fake_connect)),
            patch.object(listener_sidecar, "_write_listener_heartbeat", side_effect=lambda extra=None: heartbeats.append(dict(extra or {}))),
        ):
            result = await listener_sidecar._connect_saved_accounts()
        return result, heartbeats

    def test_connect_saved_accounts_idles_without_accounts(self):
        async def run_case():
            return await self._connect_saved_accounts_with(accounts={})

        result, heartbeats = __import__("asyncio").run(run_case())

        self.assertEqual([], result)
        self.assertIn({"status": "idle_no_accounts"}, heartbeats)

    def test_connect_saved_accounts_degrades_when_all_accounts_fail(self):
        async def run_case():
            return await self._connect_saved_accounts_with(accounts={"301299112": {}}, connected=False)

        result, heartbeats = __import__("asyncio").run(run_case())

        self.assertEqual([], result)
        self.assertIn({"status": "degraded_no_connected_accounts"}, heartbeats)

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
