import atexit
import asyncio
import copy
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


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


class _FakePhoneLoginClient:
    def __init__(self, *, code_delay=0):
        self.code_delay = code_delay
        self.connect_count = 0
        self.disconnect_count = 0
        self.send_code_count = 0
        self.sign_in_calls = []

    async def connect(self):
        self.connect_count += 1

    async def disconnect(self):
        self.disconnect_count += 1

    async def send_code_request(self, phone):
        self.send_code_count += 1
        if self.code_delay:
            await asyncio.sleep(self.code_delay)
        return SimpleNamespace(phone_code_hash="hash-123")

    async def sign_in(self, phone=None, code=None, *, phone_code_hash=None, password=None):
        self.sign_in_calls.append({
            "phone": phone,
            "code": code,
            "phone_code_hash": phone_code_hash,
            "password": password,
        })
        return SimpleNamespace(id=111)


class AccountLoginConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ui._pending_login.clear()
        ui._pending_login_locks.clear()

    async def asyncTearDown(self):
        for session_key in list(ui._pending_login.keys()):
            await ui._clear_pending_login(session_key, remove_temp_files=False)
        ui._pending_login.clear()
        ui._pending_login_locks.clear()

    async def test_qr_start_reuses_existing_pending_login(self):
        client = _FakeLoginClient()
        with patch.object(ui, "create_account_client", return_value=client) as create_mock, \
                patch.object(ui, "get_accounts", return_value={}):
            ok, _message, first_info = await ui.ui_account_login_qr_start("session-a")
            self.assertTrue(ok)
            self.assertEqual("connecting", first_info["status"])

            prepare_task = ui._pending_login["session-a"]["prepare_task"]
            await asyncio.wait_for(prepare_task, timeout=1)

            second_ok, _second_message, second_info = await ui.ui_account_login_qr_start("session-a")

        self.assertTrue(second_ok)
        self.assertEqual(1, create_mock.call_count)
        self.assertEqual(1, client.connect_count)
        self.assertEqual(1, client.qr_login_count)
        self.assertEqual("tg://login?token=test", second_info["qr_url"])
        self.assertEqual("waiting_scan", second_info["status"])

    async def test_qr_start_connect_timeout_marks_pending_error(self):
        client = _FakeLoginClient(connect_delay=0.05)
        with patch.object(ui, "create_account_client", return_value=client), \
                patch.object(ui, "ACCOUNT_LOGIN_QR_CONNECT_TIMEOUT_SEC", 0.01):
            ok, message, qr_info = await ui.ui_account_login_qr_start("session-timeout")
            prepare_task = ui._pending_login["session-timeout"]["prepare_task"]
            await asyncio.wait_for(prepare_task, timeout=1)

        self.assertTrue(ok)
        self.assertEqual("二维码生成中，请稍后", message)
        self.assertEqual("connecting", qr_info["status"])

        status = ui.ui_account_login_qr_status("session-timeout")
        self.assertEqual("error", status["status"])
        self.assertIn("Telegram 连接超时", status["message"])
        self.assertGreaterEqual(client.disconnect_count, 1)

    async def test_phone_code_send_timeout_keeps_pending_login_for_manual_code(self):
        client = _FakePhoneLoginClient(code_delay=0.05)
        with patch.object(ui, "create_account_client", return_value=client), \
                patch.object(ui, "ACCOUNT_LOGIN_ACTION_TIMEOUT_SEC", 0.01):
            ok, message, _extra = await ui.ui_account_login_start("+8613800138000", "phone-timeout")

        self.assertTrue(ok)
        self.assertIn("直接输入", message)
        pending = ui._pending_login["phone-timeout"]
        self.assertEqual("waiting_code", pending["status"])
        self.assertIsNotNone(pending["phone_code_task"])

        await asyncio.wait_for(pending["phone_code_task"], timeout=1)
        pending_after = ui._pending_login["phone-timeout"]
        self.assertEqual("waiting_code", pending_after["status"])
        self.assertEqual("hash-123", pending_after["phone_code_hash"])
        self.assertIsNone(pending_after["phone_code_task"])

    async def test_phone_pending_login_expires_and_disconnects(self):
        client = _FakePhoneLoginClient()
        with (
            patch.object(ui, "create_account_client", return_value=client),
            patch.object(ui, "ACCOUNT_LOGIN_PHONE_PENDING_TTL_SEC", 0.01),
            patch.object(ui, "_cleanup_pending_temp_session_files") as cleanup_mock,
        ):
            ok, _message, _extra = await ui.ui_account_login_start(
                "+8613800138000",
                "phone-expire",
            )
            self.assertTrue(ok)
            await asyncio.sleep(0.03)

        self.assertNotIn("phone-expire", ui._pending_login)
        self.assertGreaterEqual(client.disconnect_count, 1)
        cleanup_mock.assert_called_with("phone-expire")

    async def test_phone_verify_waits_for_delayed_code_hash(self):
        client = _FakePhoneLoginClient(code_delay=0.03)
        with patch.object(ui, "create_account_client", return_value=client), \
                patch.object(ui, "ACCOUNT_LOGIN_ACTION_TIMEOUT_SEC", 0.01):
            ok, _message, _extra = await ui.ui_account_login_start("+8613800138000", "phone-verify")

        self.assertTrue(ok)
        with patch.object(ui, "_finalize_account_login", new=AsyncMock(return_value=(True, "登录成功", 111))):
            ok, message, account_id = await ui.ui_account_login_verify("12345", "phone-verify")

        self.assertTrue(ok)
        self.assertEqual("登录成功", message)
        self.assertEqual(111, account_id)
        self.assertEqual(
            {
                "phone": "+8613800138000",
                "code": "12345",
                "phone_code_hash": "hash-123",
                "password": None,
            },
            client.sign_in_calls[-1],
        )


if __name__ == "__main__":
    unittest.main()
