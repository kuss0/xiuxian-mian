import atexit
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


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

from model import runtime


class RuntimeBackgroundTaskTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        runtime._low_priority_audit_bucket.clear()
        runtime._low_priority_audit_order.clear()
        runtime._low_priority_audit_flush_task = None

    async def asyncTearDown(self):
        pending = [task for task in list(runtime._background_tasks) if not task.done()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        runtime._background_tasks.clear()
        runtime._low_priority_audit_bucket.clear()
        runtime._low_priority_audit_order.clear()
        runtime._low_priority_audit_flush_task = None

    async def test_fire_and_forget_logs_task_exception(self):
        async def failing_task():
            raise RuntimeError("background boom")

        with patch.object(runtime, "console_log") as console_log, \
                patch.object(runtime.traceback, "print_exception") as print_exception:
            runtime._fire_and_forget(failing_task())
            await asyncio.sleep(0)
            await asyncio.sleep(0)

        self.assertEqual(set(), runtime._background_tasks)
        console_log.assert_called_once()
        self.assertIn("background boom", console_log.call_args.args[0])
        print_exception.assert_called_once()

    async def test_low_priority_audit_is_sent_as_summary(self):
        send_mock = AsyncMock(return_value=True)
        with patch.object(runtime, "_send_log_group_message", new=send_mock), \
                patch.object(runtime, "console_log"):
            queued = await runtime.send_audit_log("🌳 成熟期至 12:00，等待采摘结果", priority="low")

            self.assertTrue(queued)
            send_mock.assert_not_awaited()

            flushed = await runtime.flush_low_priority_audit_summary()

        self.assertTrue(flushed)
        send_mock.assert_awaited_once()
        message = send_mock.await_args.args[0]
        self.assertIn("低优先级日志汇总", message)
        self.assertIn("累计 1 条", message)
        self.assertIn("x1", message)
        self.assertIn("成熟期至 12:00", message)

    async def test_medium_priority_audit_sends_immediately_without_mention(self):
        send_mock = AsyncMock(return_value=True)
        with patch.object(runtime, "_send_log_group_message", new=send_mock), \
                patch.object(runtime, "console_log"):
            ok = await runtime.send_audit_log("⚠️ 野外历练超时，稍后复查", priority="medium")

        self.assertTrue(ok)
        send_mock.assert_awaited_once()
        message = send_mock.await_args.args[0]
        self.assertIn("野外历练超时", message)
        self.assertNotIn("tg://user?id=", message)

    def test_auto_priority_self_healing_audits_are_low(self):
        low_samples = [
            "⚠️ 放养回复超时，准备补发一次，原消息ID=9389437",
            "⚠️ 野外历练已出发但未收到最终结果编辑，进入下一轮，原消息ID=9386283",
            "🧘 launching 超时，已回退。",
            "🧘 闭关总结命中多个身份，已跳过：a[1], b[2]",
            "🦴 @李｜题库内超时未作答｜题库匹配 A.辟邪神雷｜题目：韩立在内殿对付玄骨时，最能克制魔修的手段是什么？",
            "🖥️ UI 已启动：('0.0.0.0', 3030)",
        ]
        for sample in low_samples:
            with self.subTest(sample=sample):
                self.assertEqual(runtime.AUDIT_PRIORITY_LOW, runtime._resolve_audit_priority(sample))

    def test_auto_priority_human_attention_still_wins(self):
        self.assertEqual(
            runtime.AUDIT_PRIORITY_HIGH,
            runtime._resolve_audit_priority("⚠️ 题库内超时未作答，需要人工处理"),
        )

    async def test_audit_push_status_reports_pending_low_priority_details(self):
        with patch.object(runtime, "_send_log_group_message", new=AsyncMock(return_value=True)), \
                patch.object(runtime, "console_log"):
            await runtime.send_audit_log("🌌 牵引星辰成功。", priority="low")

        total, kind_count = runtime.get_low_priority_audit_pending_counts()
        status_text = runtime.get_audit_push_status_text()

        self.assertEqual((1, 1), (total, kind_count))
        self.assertIn("低优先级: 进入定时汇总", status_text)
        self.assertIn("中优先级: 实时发送日志群", status_text)
        self.assertIn("高优先级: 实时发送日志群，并 @ 管理员", status_text)
        self.assertIn("待汇总: 1 条 / 1 类", status_text)
        self.assertIn("牵引星辰成功", status_text)

    async def test_high_priority_audit_mentions_admin(self):
        send_mock = AsyncMock(return_value=True)
        with patch.object(runtime, "_send_log_group_message", new=send_mock), \
                patch.object(runtime, "console_log"), \
                patch.object(runtime, "ADMIN_IDS", frozenset({123456789})):
            ok = await runtime.send_audit_log("需要人工处理：天尊状态异常", priority="high")

        self.assertTrue(ok)
        send_mock.assert_awaited_once()
        message = send_mock.await_args.args[0]
        self.assertIn("需要人工处理", message)
        self.assertIn("关注：", message)
        self.assertIn('tg://user?id=123456789', message)

    async def test_log_bot_callback_poller_backs_off_on_retry_after(self):
        stop_event = asyncio.Event()
        sleep_delays = []
        runtime._LOG_BOT_BACKOFF_UNTIL = 0

        def fake_call(method, payload=None, *, read_timeout=0):
            self.assertEqual("getUpdates", method)
            return False, None, 'HTTP 429: {"ok":false,"parameters":{"retry_after":5}}'

        async def fake_sleep(delay):
            sleep_delays.append(delay)
            stop_event.set()

        with patch.object(runtime, "LOG_BOT_TOKEN", "token"), \
                patch.object(runtime, "_call_log_bot_api", side_effect=fake_call), \
                patch.object(runtime.asyncio, "sleep", new=AsyncMock(side_effect=fake_sleep)):
            await runtime.run_log_bot_callback_poller(AsyncMock(), stop_event=stop_event)

        self.assertEqual([6.0], sleep_delays)
        self.assertGreater(runtime._LOG_BOT_BACKOFF_UNTIL, 0)

    def test_log_bot_callback_timeout_backoff_is_bounded(self):
        error_text = "timeout: read timed out"

        self.assertEqual(5.0, runtime._log_bot_poll_retry_delay(error_text, 1))
        self.assertEqual(10.0, runtime._log_bot_poll_retry_delay(error_text, 2))
        self.assertEqual(20.0, runtime._log_bot_poll_retry_delay(error_text, 3))
        self.assertEqual(40.0, runtime._log_bot_poll_retry_delay(error_text, 4))
        self.assertEqual(60.0, runtime._log_bot_poll_retry_delay(error_text, 5))
        self.assertEqual(60.0, runtime._log_bot_poll_retry_delay(error_text, 99))

    def test_log_bot_network_failure_uses_exponential_backoff_and_redacts_credentials(self):
        error_text = (
            "HTTPSConnectionPool(host='api.telegram.org', port=443): "
            "Max retries exceeded with url: /botsecret-token/getUpdates "
            "(Caused by NewConnectionError('Network is unreachable'))"
        )

        self.assertEqual(10.0, runtime._log_bot_poll_retry_delay(error_text, 2))
        with patch.object(runtime, "LOG_BOT_TOKEN", "secret-token"):
            sanitized = runtime._sanitize_log_bot_error(error_text)
        self.assertNotIn("secret-token", sanitized)
        self.assertIn("bot<redacted>/getUpdates", sanitized)

    async def test_log_bot_callback_poller_stays_within_worker_stop_budget(self):
        stop_event = asyncio.Event()
        observed = {}

        def fake_call(method, payload=None, *, read_timeout=0):
            observed.update(
                method=method,
                payload=dict(payload or {}),
                read_timeout=read_timeout,
            )
            stop_event.set()
            return True, [], ""

        with patch.object(runtime, "LOG_BOT_TOKEN", "token"), \
                patch.object(runtime, "_call_log_bot_api", side_effect=fake_call):
            await runtime.run_log_bot_callback_poller(AsyncMock(), stop_event=stop_event)

        self.assertEqual("getUpdates", observed["method"])
        self.assertEqual(runtime.LOG_BOT_POLL_SERVER_TIMEOUT_SEC, observed["payload"]["timeout"])
        self.assertEqual(runtime.LOG_BOT_POLL_READ_TIMEOUT_SEC, observed["read_timeout"])
        self.assertLess(runtime.LOG_BOT_POLL_READ_TIMEOUT_SEC, 20)

    async def test_notification_channel_uses_log_bot_without_account_fallback(self):
        runtime._LOG_BOT_BACKOFF_UNTIL = 0
        send_mock = MagicMock(return_value=(True, ""))
        with patch.object(runtime, "LOG_BOT_TOKEN", "token"), patch.object(
            runtime,
            "_send_chat_via_log_bot",
            new=send_mock,
        ):
            ok = await runtime.send_log_bot_notification(
                -1004412426741,
                "test notification",
                link_preview=False,
            )

        self.assertTrue(ok)
        send_mock.assert_called_once()
        self.assertEqual(-1004412426741, send_mock.call_args.args[0])
        self.assertEqual("test notification", send_mock.call_args.args[1])
        self.assertFalse(send_mock.call_args.kwargs["link_preview"])

    async def test_failed_low_priority_summary_restores_details(self):
        send_mock = AsyncMock(return_value=False)
        with patch.object(runtime, "_send_log_group_message", new=send_mock), \
                patch.object(runtime, "console_log"):
            await runtime.send_audit_log("🌌 牵引星辰成功。", priority="low")
            flushed = await runtime.flush_low_priority_audit_summary()

        self.assertFalse(flushed)
        self.assertEqual(1, len(runtime._low_priority_audit_bucket))
        restored = next(iter(runtime._low_priority_audit_bucket.values()))
        self.assertEqual(1, restored["count"])


if __name__ == "__main__":
    unittest.main()
