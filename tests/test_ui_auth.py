import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"

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

sys.path.insert(0, str(PROJECT_ROOT))

from model import runtime


class UiAuthTests(unittest.TestCase):
    def tearDown(self):
        runtime.clear_ui_auth_state()

    def test_issue_ui_login_token_rejects_non_admin(self):
        with patch.object(runtime, "ADMIN_IDS", frozenset({123})):
            with self.assertRaises(ValueError):
                runtime.issue_ui_login_token(456, now=1000)

    def test_redeem_ui_login_token_rejects_revoked_admin(self):
        with patch.object(runtime, "ADMIN_IDS", frozenset({123})):
            token = runtime.issue_ui_login_token(123, now=1000)

        with patch.object(runtime, "ADMIN_IDS", frozenset({999})):
            self.assertIsNone(runtime.redeem_ui_login_token(token, now=1001))

    def test_validate_ui_session_rejects_revoked_admin(self):
        with patch.object(runtime, "ADMIN_IDS", frozenset({123})):
            token = runtime.issue_ui_login_token(123, now=1000)
            session_token = runtime.redeem_ui_login_token(token, now=1001)

        with patch.object(runtime, "ADMIN_IDS", frozenset({999})):
            self.assertIsNone(runtime.validate_ui_session(session_token, now=1002))

    def test_touch_ui_session_rejects_revoked_admin(self):
        with patch.object(runtime, "ADMIN_IDS", frozenset({123})):
            token = runtime.issue_ui_login_token(123, now=1000)
            session_token = runtime.redeem_ui_login_token(token, now=1001)

        with patch.object(runtime, "ADMIN_IDS", frozenset({999})):
            self.assertIsNone(runtime.touch_ui_session(session_token, now=1002))


if __name__ == "__main__":
    unittest.main()
