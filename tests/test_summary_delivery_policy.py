import asyncio
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from model import runtime
from model.audit_summary_store import AuditSummaryStore
from model.audit_summary_health import read_summary_health


class SummaryDeliveryPolicyTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for p in (
            patch.object(runtime, "LOG_SEND_MODE", "bot"),
            patch.object(runtime, "LOG_GROUP_DELIVERY_METRICS", False),
            patch.object(runtime, "_LOG_BOT_BACKOFF_UNTIL", 0),
        ):
            p.start()
            self.addCleanup(p.stop)

    async def test_unknown_never_falls_back_even_without_metrics(self):
        errors = ["timeout: secret", "proxy error: secret", "reset", "",
                  'HTTP 502: {"ok":false,"error_code":502}',
                  'HTTP 403: <html>not a bot reply</html>',
                  'HTTP 500: {"ok":false,"error_code":403}']
        for error in errors:
            with (
                self.subTest(error=error),
                patch.object(runtime, "_send_log_group_via_bot", return_value=(False, error)) as bot,
                patch.object(runtime, "_get_any_authed_client_with_account") as account,
                patch("builtins.print") as log,
            ):
                self.assertFalse(await runtime._send_log_group_message("private body", allow_unknown_fallback=False))
                bot.assert_called_once()
                account.assert_not_called()
                self.assertNotIn("private body", str(log.call_args_list))
                self.assertNotIn("secret", str(log.call_args_list))

    async def test_explicit_rejection_retains_existing_fallback_and_backoff(self):
        error = 'HTTP 429: {"ok":false,"error_code":429,"parameters":{"retry_after":60}}'
        with (
            patch.object(runtime, "_send_log_group_via_bot", return_value=(False, error)),
            patch.object(runtime, "_get_any_authed_client_with_account", return_value=(1, MagicMock())) as account,
            patch.object(runtime, "_run_account_rpc", new=AsyncMock()) as rpc,
        ):
            self.assertTrue(await runtime._send_log_group_message("summary", allow_unknown_fallback=False))
            account.assert_called_once()
            rpc.assert_awaited_once()
            self.assertGreater(runtime._LOG_BOT_BACKOFF_UNTIL, 0)

    async def test_success_and_classifier_failure(self):
        with (
            patch.object(runtime, "_send_log_group_via_bot", return_value=(True, "")) as bot,
            patch.object(runtime, "bot_delivery_outcome", side_effect=ValueError("classifier failed")) as classify,
            patch.object(runtime, "_get_any_authed_client_with_account") as account,
        ):
            self.assertTrue(await runtime._send_log_group_message("summary", allow_unknown_fallback=False))
            classify.assert_not_called()
            bot.return_value = (False, '{"ok":false,"error_code":400}')
            self.assertFalse(await runtime._send_log_group_message("summary", allow_unknown_fallback=False))
            account.assert_not_called()

    async def test_outer_timeout_does_not_race_late_bot_completion(self):
        release, completed = threading.Event(), threading.Event()
        def slow_bot(*_args, **_kwargs):
            release.wait(2)
            completed.set()
            return True, ""
        with (
            patch.object(runtime, "LOG_BOT_TOTAL_TIMEOUT_SEC", 0.01),
            patch.object(runtime, "_send_log_group_via_bot", side_effect=slow_bot),
            patch.object(runtime, "_get_any_authed_client_with_account") as account,
        ):
            try:
                self.assertFalse(await runtime._send_log_group_message("summary", allow_unknown_fallback=False))
                account.assert_not_called()
            finally:
                release.set()
                self.assertTrue(await asyncio.to_thread(completed.wait, 2))

    async def test_exception_and_cancellation_do_not_fallback(self):
        for exc in (RuntimeError("failed"), asyncio.CancelledError()):
            with (
                self.subTest(exc=type(exc).__name__),
                patch.object(runtime, "_send_log_group_via_bot", side_effect=exc),
                patch.object(runtime, "_get_any_authed_client_with_account") as account,
            ):
                if isinstance(exc, asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError):
                        await runtime._send_log_group_message("summary", allow_unknown_fallback=False)
                else:
                    self.assertFalse(await runtime._send_log_group_message("summary", allow_unknown_fallback=False))
                account.assert_not_called()

    async def test_default_urgent_path_keeps_existing_fallback(self):
        with (
            patch.object(runtime, "_send_log_group_via_bot", return_value=(False, "timeout")),
            patch.object(runtime, "_get_any_authed_client_with_account", return_value=(1, MagicMock())) as account,
            patch.object(runtime, "_run_account_rpc", new=AsyncMock()),
            patch.object(runtime, "console_log"),
        ):
            self.assertTrue(await runtime.send_audit_log("needs human action", priority="high"))
            account.assert_called_once()

    async def test_structured_store_holds_unknown_and_observer_reports_it(self):
        with TemporaryDirectory() as directory:
            now = [1000.0]
            path = Path(directory) / "summary.db"
            store = AuditSummaryStore(path, {}, [], clock=lambda: now[0])
            with (
                patch.object(runtime, "LOG_GROUP_STRUCTURED_SUMMARY", True),
                patch.object(runtime, "_audit_summary_store", store),
                patch.object(runtime, "_low_priority_audit_bucket", store.bucket),
                patch.object(runtime, "_low_priority_audit_order", store.order),
                patch.object(runtime, "_schedule_low_priority_audit_flush"),
                patch.object(runtime, "console_log"),
                patch.object(runtime, "_send_log_group_via_bot", return_value=(False, "timeout")) as bot,
                patch.object(runtime, "_get_any_authed_client_with_account") as account,
            ):
                await runtime.send_audit_log("confirmed cultivation", priority="normal", summary_kind="yuanying")
                now[0] += 1800
                self.assertFalse(await runtime.flush_low_priority_audit_summary())
                self.assertEqual(1, len(store.held))
                restarted = AuditSummaryStore(path, {}, [], clock=lambda: now[0])
                await restarted.resume(1800)
                now[0] += 1800
                await restarted.flush(lambda _rows: "should not send", AsyncMock(side_effect=AssertionError("replay")), 1800)
                self.assertEqual(1, read_summary_health(path, now[0])["unresolved_batches"])
                bot.assert_called_once()
                account.assert_not_called()

    async def test_account_mode_and_preexisting_bot_backoff_still_send_once(self):
        for mode, until in (("account", 0), ("bot", float("inf"))):
            with (
                self.subTest(mode=mode),
                patch.object(runtime, "LOG_SEND_MODE", mode),
                patch.object(runtime, "_LOG_BOT_BACKOFF_UNTIL", until),
                patch.object(runtime, "_send_log_group_via_bot") as bot,
                patch.object(runtime, "_get_any_authed_client_with_account", return_value=(1, MagicMock())),
                patch.object(runtime, "_run_account_rpc", new=AsyncMock()) as rpc,
            ):
                self.assertTrue(await runtime._send_log_group_message("summary", allow_unknown_fallback=False))
                bot.assert_not_called()
                rpc.assert_awaited_once()
