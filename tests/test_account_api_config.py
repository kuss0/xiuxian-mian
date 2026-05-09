import atexit
import copy
import os
import sys
import unittest
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
                "ADMIN_ID=0",
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


if __name__ == "__main__":
    unittest.main()
