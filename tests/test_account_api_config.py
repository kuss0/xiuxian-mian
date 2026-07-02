import atexit
import asyncio
import copy
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"
CREATED_ENV = False

if not ENV_PATH.exists():
    ENV_PATH.write_text(
        "\n".join(
            [
                "API_ID=12345",
                "API_HASH=00000000000000000000000000000000",
                "TG_PROXY_TYPE=",
                "TG_PROXY_HOST=127.0.0.1:7890",
                "LOG_GROUP_ID=0",
                "LOG_SEND_MODE=account",
                "ADMIN_ID=1",
                "CHAOGU_UI_HOST=127.0.0.1",
                "CHAOGU_UI_PORT=3030",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    CREATED_ENV = True

if CREATED_ENV:
    atexit.register(lambda: ENV_PATH.exists() and ENV_PATH.unlink())

sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model import ui


class AccountApiConfigTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

    def test_parse_login_api_requires_pair(self):
        self.assertEqual((None, None), ui._parse_account_login_api("", ""))
        self.assertEqual((24680, "hash-value"), ui._parse_account_login_api("24680", "hash-value"))
        with self.assertRaises(ValueError):
            ui._parse_account_login_api("24680", "")
        with self.assertRaises(ValueError):
            ui._parse_account_login_api("abc", "hash-value")

    def test_runtime_account_snapshot_hides_api_hash(self):
        state_module.set_accounts({
            "111": {
                "session": "account_111",
                "username": "u111",
                "api_id": 24680,
                "api_hash": "secret-hash",
            }
        })
        with patch.object(ui, "get_all_clients", return_value={}):
            snapshot = ui._get_runtime_accounts_snapshot()
        account = snapshot["111"]
        self.assertEqual("custom", account["api_source"])
        self.assertEqual(24680, account["api_id"])
        self.assertNotIn("api_hash", account)


class _FakeQrLogin:
    def __init__(self, url="tg://login?token=test"):
        self.url = url
        self.expires = datetime.now(timezone.utc) + timedelta(minutes=2)

    async def wait(self):
        await asyncio.Event().wait()


class _FakeLoginClient:
    def __init__(self, *, connect_delay=0):
        self.connect_delay = connect_delay
        self.connect_count = 0
        self.qr_login_count = 0
        self.disconnect_count = 0

    async def connect(self):
        self.connect_count += 1
        if self.connect_delay:
            await asyncio.sleep(self.connect_delay)

    async def disconnect(self):
        self.disconnect_count += 1

    async def qr_login(self, ignored_ids=None):
        self.qr_login_count += 1
        return _FakeQrLogin()


class AccountLoginConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ui._pending_login.clear()

    async def asyncTearDown(self):
        for session_key in list(ui._pending_login.keys()):
            await ui._clear_pending_login(session_key, remove_temp_files=False)
        ui._pending_login.clear()

    async def test_qr_start_reuses_existing_pending_login(self):
        client = _FakeLoginClient()
        with patch.object(ui, "create_account_client", return_value=client) as create_mock, \
                patch.object(ui, "get_accounts", return_value={}):
            ok, _message, first_info = await ui.ui_account_login_qr_start("session-a")
            second_ok, _second_message, second_info = await ui.ui_account_login_qr_start("session-a")

        self.assertTrue(ok)
        self.assertTrue(second_ok)
        self.assertEqual(1, create_mock.call_count)
        self.assertEqual(1, client.connect_count)
        self.assertEqual(1, client.qr_login_count)
        self.assertEqual(first_info["qr_url"], second_info["qr_url"])
        self.assertEqual("waiting_scan", second_info["status"])

    async def test_qr_start_connect_timeout_clears_pending_login(self):
        client = _FakeLoginClient(connect_delay=0.05)
        with patch.object(ui, "create_account_client", return_value=client), \
                patch.object(ui, "ACCOUNT_LOGIN_CONNECT_TIMEOUT_SEC", 0.01):
            ok, message, qr_info = await ui.ui_account_login_qr_start("session-timeout")

        self.assertFalse(ok)
        self.assertIn("Telegram 连接超时", message)
        self.assertIsNone(qr_info)
        self.assertNotIn("session-timeout", ui._pending_login)
        self.assertGreaterEqual(client.disconnect_count, 1)


if __name__ == "__main__":
    unittest.main()
