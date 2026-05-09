import atexit
import os
import sys
import unittest
from pathlib import Path


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

from model import control
from model.config import RE_CMD_HELP, RE_CMD_SINGLE_STATUS_PATTERNS


class LogGroupDisplayTests(unittest.TestCase):
    def test_help_regex_accepts_command_aliases(self):
        self.assertIsNotNone(RE_CMD_HELP.match(".指令"))
        self.assertIsNotNone(RE_CMD_HELP.match(".帮助"))
        self.assertIsNotNone(RE_CMD_HELP.match(".help"))

    def test_status_patterns_include_recent_modules(self):
        module_names = {name for _pattern, name in RE_CMD_SINGLE_STATUS_PATTERNS}
        self.assertIn("第二元神", module_names)
        self.assertIn("太一", module_names)

    def test_log_group_card_escapes_html(self):
        html_text = control._format_log_group_card_html("状态<标题>", "a < b & c")
        self.assertIn("状态&lt;标题&gt;", html_text)
        self.assertIn("a &lt; b &amp; c", html_text)
        self.assertIn("<pre>", html_text)

    def test_help_mentions_safe_status_and_selector(self):
        html_text = control._format_log_group_help_html()
        self.assertIn(".状态", html_text)
        self.assertIn("@昵称", html_text)
        self.assertIn("全局锁", html_text)


if __name__ == "__main__":
    unittest.main()
