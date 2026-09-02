import sys
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model.features import daily_grow


ACCOUNTS = {
    "301299112": {"username": "jfdffdddd"},
    "8659059191": {"username": "WalterWA2000"},
    "7538826434": {"username": "Lpprceqei"},
    "8574677796": {"username": "wisemole"},
    "8613500668": {"username": "xianxia9527"},
}


class DailyGrowTargetTests(unittest.TestCase):
    def test_jfdffdddd_is_never_a_target(self):
        with patch.object(daily_grow, "get_accounts", return_value=ACCOUNTS), patch.object(
            daily_grow, "DAILY_GROW_ACCOUNT_ALLOWLIST", frozenset()
        ):
            targets = daily_grow.target_account_ids()
        self.assertNotIn(301299112, targets)
        self.assertEqual([7538826434, 8574677796, 8613500668, 8659059191], targets)

    def test_allowlist_narrows_the_target_set(self):
        """白名单是试发用的收窄器。显式指定，不依赖当前部署值。"""
        with patch.object(daily_grow, "get_accounts", return_value=ACCOUNTS), patch.object(
            daily_grow, "DAILY_GROW_ACCOUNT_ALLOWLIST", frozenset({8613500668})
        ):
            self.assertEqual([8613500668], daily_grow.target_account_ids())

    def test_allowlist_never_overrides_the_exclusion(self):
        """就算把 jfdffdddd 写进白名单也不能发 —— 排除优先。"""
        with patch.object(daily_grow, "get_accounts", return_value=ACCOUNTS), patch.object(
            daily_grow, "DAILY_GROW_ACCOUNT_ALLOWLIST", frozenset({301299112})
        ):
            self.assertEqual([], daily_grow.target_account_ids())

    def test_current_deployment_targets_all_four_real_accounts(self):
        with patch.object(daily_grow, "get_accounts", return_value=ACCOUNTS):
            self.assertEqual(
                [7538826434, 8574677796, 8613500668, 8659059191],
                daily_grow.target_account_ids(),
            )

    def test_due_time_is_stable_within_a_day_and_inside_the_window(self):
        now = time.time()
        first = daily_grow.account_due_ts(8659059191, now)
        self.assertEqual(first, daily_grow.account_due_ts(8659059191, now + 60))
        hour = time.localtime(first).tm_hour
        self.assertGreaterEqual(hour, daily_grow.DAILY_GROW_WINDOW_START_HOUR)
        self.assertLess(hour, daily_grow.DAILY_GROW_WINDOW_END_HOUR)

    def test_accounts_do_not_share_one_due_minute(self):
        now = time.time()
        with patch.object(daily_grow, "get_accounts", return_value=ACCOUNTS), patch.object(
            daily_grow, "DAILY_GROW_ACCOUNT_ALLOWLIST", frozenset()
        ):
            due = [daily_grow.account_due_ts(a, now) for a in daily_grow.target_account_ids()]
        self.assertEqual(len(due), len({int(ts // 60) for ts in due}))


class DailyGrowSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        daily_grow._last_send_ts = 0.0

    async def test_disabled_switch_sends_nothing(self):
        with patch.object(daily_grow, "DAILY_GROW_ENABLED", False), patch.object(
            daily_grow, "_send_grow", new=AsyncMock()
        ) as send_mock:
            self.assertFalse(await daily_grow.run_daily_grow_scheduler(time.time()))
        send_mock.assert_not_awaited()

    async def _run_once(self, *, last_sent=None, offline=False, ok=True):
        now = time.time()
        # 把发送时刻推到过去，确保已到点
        with patch.object(daily_grow, "DAILY_GROW_ENABLED", True), patch.object(
            daily_grow, "get_accounts", return_value=ACCOUNTS
        ), patch.object(
            daily_grow, "DAILY_GROW_ACCOUNT_ALLOWLIST", frozenset({8613500668})
        ), patch.object(
            daily_grow, "account_due_ts", return_value=now - 3600
        ), patch.object(
            daily_grow, "get_daily_grow_last_sent", return_value=dict(last_sent or {})
        ), patch.object(
            daily_grow, "is_account_offline", return_value=offline
        ), patch.object(
            daily_grow, "set_daily_grow_last_sent"
        ) as save_mock, patch.object(
            daily_grow, "save_state"
        ), patch.object(
            daily_grow, "console_log"
        ), patch.object(
            daily_grow, "_send_grow", new=AsyncMock(return_value=(ok, "msg=1"))
        ) as send_mock:
            fired = await daily_grow.run_daily_grow_scheduler(now)
        return fired, send_mock, save_mock, now

    async def test_sends_one_account_per_tick(self):
        fired, send_mock, _save, _now = await self._run_once()
        self.assertTrue(fired)
        self.assertEqual(1, send_mock.await_count)
        self.assertEqual(8613500668, send_mock.await_args.args[0])

    async def test_already_sent_today_is_skipped(self):
        day = time.strftime("%Y-%m-%d", time.localtime())
        fired, send_mock, _save, _now = await self._run_once(
            last_sent={"8613500668": day}
        )
        self.assertFalse(fired)
        send_mock.assert_not_awaited()

    async def test_offline_account_is_skipped(self):
        fired, send_mock, _save, _now = await self._run_once(offline=True)
        self.assertFalse(fired)
        send_mock.assert_not_awaited()

    async def test_failed_send_is_still_recorded_so_it_does_not_retry(self):
        fired, send_mock, save_mock, _now = await self._run_once(ok=False)
        self.assertTrue(fired)
        self.assertEqual(1, send_mock.await_count)
        recorded = save_mock.call_args.args[0]
        self.assertIn("8613500668", recorded)

    async def test_min_gap_blocks_a_second_send_in_the_same_window(self):
        await self._run_once()
        fired, send_mock, _save, _now = await self._run_once()
        self.assertFalse(fired)
        send_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
