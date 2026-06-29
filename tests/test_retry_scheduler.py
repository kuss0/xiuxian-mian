import asyncio
import atexit
import copy
import os
import sys
import unittest
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

from model import config
from model import runtime
from model import state as state_module


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._bot_health_snapshot = (
            runtime._bot_health_state,
            runtime._bot_health_reason,
            runtime._bot_health_changed_at,
            runtime._bot_waiting_since,
            runtime._bot_last_seen_at,
            runtime._bot_probe_sent_at,
            runtime._bot_last_block_log_at,
        )

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        (
            runtime._bot_health_state,
            runtime._bot_health_reason,
            runtime._bot_health_changed_at,
            runtime._bot_waiting_since,
            runtime._bot_last_seen_at,
            runtime._bot_probe_sent_at,
            runtime._bot_last_block_log_at,
        ) = self._bot_health_snapshot
        super().tearDown()


class RetrySchedulerTests(_StateIsolationMixin, unittest.TestCase):
    def test_default_retry_limit_is_one_resend(self):
        self.assertEqual(1, config.RETRY_LIMIT)
        self.assertEqual(1, runtime.RETRY_LIMIT)

    def test_default_pending_stops_after_one_resend(self):
        send_as_id = 971001
        now = 5000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                101: {
                    "cmd": ".测试指令",
                    "sent_at": now - 20,
                    "retry": 1,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": "normal",
                }
            }

        with patch.object(runtime, "should_pause_for_bot_health", return_value=False), \
             patch.object(runtime, "get_bot_last_seen_at", return_value=now), \
             patch.object(runtime, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(runtime, "send_audit_log", new=AsyncMock()):
            asyncio.run(runtime.run_retry_scheduler(now, send_as_id=send_as_id))

        send_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id) as identity_state:
            self.assertEqual({}, identity_state["pending_tasks"])

    def test_default_pending_resends_once_with_retry_priority(self):
        send_as_id = 971002
        now = 6000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                201: {
                    "cmd": ".测试指令",
                    "sent_at": now - 20,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": "normal",
                }
            }

        async def fake_send(command, **kwargs):
            with state_module.use_identity(send_as_id) as identity_state:
                identity_state["pending_tasks"][202] = {
                    "cmd": command,
                    "sent_at": now + 1,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": kwargs.get("priority"),
                }
            return SimpleNamespace(id=202, sent_at=now + 1)

        with patch.object(runtime, "should_pause_for_bot_health", return_value=False), \
             patch.object(runtime, "get_bot_last_seen_at", return_value=now), \
             patch.object(runtime, "send_game_command", side_effect=fake_send) as send_mock, \
             patch.object(runtime, "send_audit_log", new=AsyncMock()):
            asyncio.run(runtime.run_retry_scheduler(now, send_as_id=send_as_id))

        send_mock.assert_awaited_once_with(
            ".测试指令",
            send_as_id=send_as_id,
            priority=runtime.SEND_PRIORITY_RETRY,
            max_retry=1,
            reply_timeout=10,
        )
        with state_module.use_identity(send_as_id) as identity_state:
            self.assertNotIn(201, identity_state["pending_tasks"])
            self.assertEqual(1, identity_state["pending_tasks"][202]["retry"])
            self.assertEqual(1, identity_state["pending_tasks"][202]["max_retry"])

    def test_pending_retry_preserves_send_intent_metadata(self):
        send_as_id = 971005
        now = 6500.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                251: {
                    "cmd": ".引道 水",
                    "sent_at": now - 20,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": "chain",
                    "source_module": "太一",
                    "op_id": "taiyi-yindao-251",
                    "chain_id": "taiyi-cycle-1",
                    "delete_policy": "auto_delete",
                }
            }

        async def fake_send(command, **kwargs):
            return SimpleNamespace(id=252, sent_at=now + 1)

        with patch.object(runtime, "should_pause_for_bot_health", return_value=False), \
             patch.object(runtime, "get_bot_last_seen_at", return_value=now), \
             patch.object(runtime, "send_game_command", side_effect=fake_send) as send_mock, \
             patch.object(runtime, "send_audit_log", new=AsyncMock()):
            asyncio.run(runtime.run_retry_scheduler(now, send_as_id=send_as_id))

        send_mock.assert_awaited_once_with(
            ".引道 水",
            send_as_id=send_as_id,
            priority=runtime.SEND_PRIORITY_RETRY,
            max_retry=1,
            reply_timeout=10,
            source_module="太一",
            op_id="taiyi-yindao-251",
            chain_id="taiyi-cycle-1",
            delete_policy="auto_delete",
        )

    def test_pending_retry_preserves_reply_to_message(self):
        send_as_id = 971008
        now = 6550.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                271: {
                    "cmd": ".灵树灌溉",
                    "sent_at": now - 20,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 123456,
                    "priority": "normal",
                }
            }

        async def fake_send(command, **kwargs):
            return SimpleNamespace(id=272, sent_at=now + 1)

        with patch.object(runtime, "should_pause_for_bot_health", return_value=False), \
             patch.object(runtime, "get_bot_last_seen_at", return_value=now), \
             patch.object(runtime, "send_game_command", side_effect=fake_send) as send_mock, \
             patch.object(runtime, "send_audit_log", new=AsyncMock()):
            asyncio.run(runtime.run_retry_scheduler(now, send_as_id=send_as_id))

        send_mock.assert_awaited_once_with(
            ".灵树灌溉",
            send_as_id=send_as_id,
            priority=runtime.SEND_PRIORITY_RETRY,
            max_retry=1,
            reply_timeout=10,
            reply_to=123456,
        )

    def test_pending_timeout_without_bot_seen_marks_suspect_and_does_not_resend(self):
        send_as_id = 971006
        now = 6600.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                261: {
                    "cmd": ".灵树状态",
                    "sent_at": now - 20,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": "normal",
                }
            }

        with patch.object(runtime, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(runtime, "send_audit_log", new=AsyncMock()):
            runtime._bot_last_seen_at = now - 30
            asyncio.run(runtime.run_retry_scheduler(now, send_as_id=send_as_id))

        send_mock.assert_not_awaited()
        self.assertEqual(runtime.BOT_HEALTH_SUSPECT, runtime.get_bot_health_snapshot()["state"])
        with state_module.use_identity(send_as_id) as identity_state:
            self.assertEqual({}, identity_state["pending_tasks"])

    def test_legacy_command_key_pending_resends_once(self):
        send_as_id = 971003
        now = 7000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                301: {
                    "command": ".旧结构指令",
                    "sent_at": now - 20,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": "normal",
                }
            }

        async def fake_send(command, **kwargs):
            with state_module.use_identity(send_as_id) as identity_state:
                identity_state["pending_tasks"][302] = {
                    "cmd": command,
                    "sent_at": now + 1,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": kwargs.get("priority"),
                }
            return SimpleNamespace(id=302, sent_at=now + 1)

        with patch.object(runtime, "should_pause_for_bot_health", return_value=False), \
             patch.object(runtime, "get_bot_last_seen_at", return_value=now), \
             patch.object(runtime, "send_game_command", side_effect=fake_send) as send_mock, \
             patch.object(runtime, "send_audit_log", new=AsyncMock()):
            asyncio.run(runtime.run_retry_scheduler(now, send_as_id=send_as_id))

        send_mock.assert_awaited_once_with(
            ".旧结构指令",
            send_as_id=send_as_id,
            priority=runtime.SEND_PRIORITY_RETRY,
            max_retry=1,
            reply_timeout=10,
        )
        with state_module.use_identity(send_as_id) as identity_state:
            self.assertNotIn(301, identity_state["pending_tasks"])
            self.assertEqual(".旧结构指令", identity_state["pending_tasks"][302]["cmd"])
            self.assertEqual(1, identity_state["pending_tasks"][302]["retry"])

    def test_empty_command_pending_is_dropped_without_crashing_scheduler(self):
        send_as_id = 971004
        now = 8000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                401: {
                    "sent_at": now - 20,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": "normal",
                }
            }

        with patch.object(runtime, "should_pause_for_bot_health", return_value=False), \
             patch.object(runtime, "get_bot_last_seen_at", return_value=now), \
             patch.object(runtime, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(runtime, "send_audit_log", new=AsyncMock()):
            asyncio.run(runtime.run_retry_scheduler(now, send_as_id=send_as_id))

        send_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id) as identity_state:
            self.assertEqual({}, identity_state["pending_tasks"])

    def test_zero_retry_pending_is_dropped_without_resend(self):
        send_as_id = 971007
        now = 8100.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                402: {
                    "cmd": f"{config.CMD_STARGAZER_GUIDE} 天雷星",
                    "sent_at": now - 20,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": "normal",
                    "max_retry": 0,
                }
            }

        with patch.object(runtime, "should_pause_for_bot_health", return_value=False), \
             patch.object(runtime, "get_bot_last_seen_at", return_value=now), \
             patch.object(runtime, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(runtime, "send_audit_log", new=AsyncMock()) as audit_mock:
            asyncio.run(runtime.run_retry_scheduler(now, send_as_id=send_as_id))

        send_mock.assert_not_awaited()
        audit_mock.assert_awaited_once()
        self.assertIn("已停补发", audit_mock.await_args.args[0])
        with state_module.use_identity(send_as_id) as identity_state:
            self.assertEqual({}, identity_state["pending_tasks"])

    def test_divination_zero_retry_timeout_is_module_managed_without_audit(self):
        send_as_id = 971009
        now = 8200.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                403: {
                    "cmd": config.CMD_DIVINATION,
                    "sent_at": now - 20,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": "normal",
                    "max_retry": 0,
                    "source_module": "卜筮问天",
                    "op_id": "divination_query:971009:2026-06-09:3:try4",
                    "chain_id": "divination:971009:2026-06-09",
                }
            }

        with patch.object(runtime, "should_pause_for_bot_health", return_value=False), \
             patch.object(runtime, "get_bot_last_seen_at", return_value=now), \
             patch.object(runtime, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(runtime, "send_audit_log", new=AsyncMock()) as audit_mock, \
             patch.object(runtime, "console_log") as console_mock:
            asyncio.run(runtime.run_retry_scheduler(now, send_as_id=send_as_id))

        send_mock.assert_not_awaited()
        audit_mock.assert_not_awaited()
        self.assertIn("交由模块状态机继续", console_mock.call_args.args[0])
        with state_module.use_identity(send_as_id) as identity_state:
            self.assertEqual({}, identity_state["pending_tasks"])

    def test_small_world_query_zero_retry_timeout_is_module_managed_without_resend(self):
        send_as_id = 971010
        now = 8300.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                404: {
                    "cmd": config.CMD_SMALL_WORLD_QUERY,
                    "sent_at": now - 400,
                    "retry": 0,
                    "timeout": 300,
                    "reply_to_msg_id": 0,
                    "priority": "chain",
                    "max_retry": 0,
                    "source_module": "小世界",
                }
            }

        with patch.object(runtime, "should_pause_for_bot_health", return_value=False), \
             patch.object(runtime, "get_bot_last_seen_at", return_value=now), \
             patch.object(runtime, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(runtime, "send_audit_log", new=AsyncMock()) as audit_mock, \
             patch.object(runtime, "console_log") as console_mock:
            asyncio.run(runtime.run_retry_scheduler(now, send_as_id=send_as_id))

        send_mock.assert_not_awaited()
        audit_mock.assert_not_awaited()
        self.assertIn("交由模块状态机继续", console_mock.call_args.args[0])
        with state_module.use_identity(send_as_id) as identity_state:
            self.assertEqual({}, identity_state["pending_tasks"])

    def test_hehuan_zero_retry_timeout_is_module_managed_without_audit(self):
        send_as_id = 971011
        now = 8400.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                405: {
                    "cmd": f"{config.CMD_HEHUAN_DUAL} 温养",
                    "sent_at": now - 400,
                    "retry": 0,
                    "timeout": 300,
                    "reply_to_msg_id": 8801,
                    "priority": "normal",
                    "max_retry": 0,
                    "source_module": "合欢宗",
                }
            }

        with patch.object(runtime, "should_pause_for_bot_health", return_value=False), \
             patch.object(runtime, "get_bot_last_seen_at", return_value=now), \
             patch.object(runtime, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(runtime, "send_audit_log", new=AsyncMock()) as audit_mock, \
             patch.object(runtime, "console_log") as console_mock:
            asyncio.run(runtime.run_retry_scheduler(now, send_as_id=send_as_id))

        send_mock.assert_not_awaited()
        audit_mock.assert_not_awaited()
        self.assertIn("交由模块状态机继续", console_mock.call_args.args[0])
        with state_module.use_identity(send_as_id) as identity_state:
            self.assertEqual({}, identity_state["pending_tasks"])

    def test_retry_priority_gap_is_one_to_three_seconds(self):
        self.assertEqual((1.0, 3.0), runtime._get_send_gap_range(runtime.SEND_PRIORITY_RETRY))


if __name__ == "__main__":
    unittest.main()
