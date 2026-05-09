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
from model.features import tianti


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class TiantiEnhancementTests(_StateIsolationMixin, unittest.TestCase):
    def test_panel_parses_gangfeng_cooldown_and_wenxin_status(self):
        send_as_id = 95001
        now = 1000.0
        state_module.ensure_identity_registered(send_as_id)

        text = (
            "【凌霄云阶】\n"
            "当前进度：3/12 阶\n"
            "已完成周天：2 轮\n"
            "罡风淬体：1/12 层\n"
            "登阶冷却：1小时\n"
            "问心状态：今日尚未问心\n"
            ".引九天罡风：11小时59分钟32秒"
        )
        with state_module.use_identity(send_as_id), patch.object(tianti.random, "randint", return_value=5):
            payload = tianti._parse_tianti_panel(text)
            changed = tianti._apply_tianti_panel_payload(payload, now=now)

            self.assertTrue(changed)
            self.assertEqual(3, state_module.state["tianti_progress_current"])
            self.assertEqual(12, state_module.state["tianti_progress_total"])
            self.assertEqual(2, state_module.state["tianti_cycle_count"])
            self.assertEqual(1, state_module.state["tianti_gangfeng_level"])
            self.assertGreater(state_module.state["next_tianti_climb_time"], now + 3600)
            self.assertGreater(state_module.state["next_tianti_gangfeng_time"], now + 11 * 3600)
            self.assertEqual("", state_module.state["tianti_last_wenxin_day"])

    def test_ready_panel_does_not_override_pending_climb(self):
        send_as_id = 95002
        now = 2000.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["next_tianti_climb_time"] = now + 3600
            state_module.state["pending_tasks"] = {
                123: {"cmd": ".登天阶", "sent_at": now - 10, "retry": 0}
            }
            payload = {"cooldown_text": "可立即登阶"}
            changed = tianti._apply_tianti_panel_payload(payload, now=now)

            self.assertTrue(changed)
            self.assertEqual(now + 3600, state_module.state["next_tianti_climb_time"])

    def test_estimated_wenxin_window_text(self):
        send_as_id = 95003
        now = 3000.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tianti_wenxin_enabled"] = True
            state_module.state["tianti_progress_current"] = 2
            state_module.state["tianti_progress_total"] = 12
            state_module.state["tianti_remaining_climb_count"] = 3
            state_module.state["tianti_theoretical_max_stage"] = 5
            state_module.state["tianti_wenxin_trigger_stage"] = 4
            state_module.state["next_tianti_climb_time"] = now + 3600

            text = tianti.get_tianti_estimated_wenxin_window_text(now)

            self.assertIn("触发阶 4", text)
            self.assertIn("10 分钟", text)


if __name__ == "__main__":
    unittest.main()
