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
        self._flood_wait_snapshot = (
            dict(runtime._ACCOUNT_FLOOD_WAIT_UNTIL),
            dict(runtime._ACCOUNT_FLOOD_WAIT_REASON),
            dict(runtime._ACCOUNT_FLOOD_WAIT_LAST_LOG_AT),
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
        runtime._ACCOUNT_FLOOD_WAIT_UNTIL.clear()
        runtime._ACCOUNT_FLOOD_WAIT_UNTIL.update(self._flood_wait_snapshot[0])
        runtime._ACCOUNT_FLOOD_WAIT_REASON.clear()
        runtime._ACCOUNT_FLOOD_WAIT_REASON.update(self._flood_wait_snapshot[1])
        runtime._ACCOUNT_FLOOD_WAIT_LAST_LOG_AT.clear()
        runtime._ACCOUNT_FLOOD_WAIT_LAST_LOG_AT.update(self._flood_wait_snapshot[2])
        super().tearDown()


class RetrySchedulerTests(_StateIsolationMixin, unittest.TestCase):
    def test_maintenance_passive_trigger_gate_is_exact(self):
        with patch.object(runtime, "get_global_enabled", return_value=False), \
                patch.object(runtime, "get_global_pause_source", return_value="tianzun_maintenance"):
            self.assertTrue(runtime._allows_maintenance_passive_trigger(
                "在",
                allow_maintenance_pause=True,
                intent={"source_module": "被动结算触发"},
            ))
            self.assertFalse(runtime._allows_maintenance_passive_trigger(
                ".元婴状态",
                allow_maintenance_pause=True,
                intent={"source_module": "被动结算触发"},
            ))
            self.assertFalse(runtime._allows_maintenance_passive_trigger(
                "在",
                allow_maintenance_pause=True,
                intent={"source_module": "元婴"},
            ))

    def test_default_retry_limit_is_one_resend(self):
        self.assertEqual(1, config.RETRY_LIMIT)
        self.assertEqual(1, runtime.RETRY_LIMIT)

    def test_hehuan_send_timeout_audit_is_low_priority_only_for_warm(self):
        self.assertEqual(
            runtime.AUDIT_PRIORITY_LOW,
            runtime._send_timeout_audit_priority(
                f"{config.CMD_HEHUAN_DUAL} 温养",
                {"source_module": "合欢宗"},
            ),
        )
        self.assertEqual(
            "auto",
            runtime._send_timeout_audit_priority(
                ".野外历练 深入",
                {"source_module": "天星宗"},
            ),
        )

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

    def test_account_flood_wait_blocks_all_priorities_before_send(self):
        send_as_id = 971013
        account_id = 881013
        now = 6200.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.set_identity_account(send_as_id, account_id)
        runtime._mark_account_flood_wait(account_id, 300, now=now)

        with patch.object(runtime.time, "time", return_value=now + 10), \
             patch.object(runtime, "_dungeon_quiet_blocks_send", new=AsyncMock(return_value=False)), \
             patch.object(runtime, "send_audit_log", new=AsyncMock()) as audit_mock, \
             patch.object(runtime, "_run_game_command_pre_send_guards", new=AsyncMock(return_value=(True, "", ""))):
            normal_msg = asyncio.run(runtime.send_game_command(".测试指令", send_as_id=send_as_id, priority=runtime.SEND_PRIORITY_NORMAL))
            p0_msg = asyncio.run(runtime.send_game_command(".验证 ABC 1", send_as_id=send_as_id, priority=runtime.SEND_PRIORITY_P0))
            block = runtime.get_last_game_send_block(send_as_id, ".验证 ABC 1")

        self.assertIsNone(normal_msg)
        self.assertIsNone(p0_msg)
        self.assertGreaterEqual(audit_mock.await_count, 1)
        self.assertEqual("flood_wait_backoff", block.get("code"))

    def test_pending_retry_keeps_original_pending_when_resend_not_sent(self):
        send_as_id = 971012
        now = 6100.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                211: {
                    "cmd": ".测试指令",
                    "sent_at": now - 20,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": "normal",
                }
            }

        with patch.object(runtime, "should_pause_for_bot_health", return_value=False), \
             patch.object(runtime, "get_bot_last_seen_at", return_value=now), \
             patch.object(runtime, "send_game_command", new=AsyncMock(return_value=None)) as send_mock, \
             patch.object(runtime, "get_last_game_send_block", return_value={"code": "send_queue_timeout", "reason": ">60s"}), \
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
            pending = identity_state["pending_tasks"]
            self.assertIn(211, pending)
            self.assertEqual(0, pending[211]["retry"])
            self.assertEqual(now, pending[211]["sent_at"])
            self.assertEqual(1, pending[211]["retry_send_blocked_count"])
            self.assertEqual("send_queue_timeout", pending[211]["retry_send_blocked_code"])

    def test_pending_retry_recovers_logged_reply_before_resend(self):
        send_as_id = 971014
        now = 6300.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                231: {
                    "cmd": ".元婴状态",
                    "sent_at": now - 40,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": "normal",
                }
            }

        recovered_reply = {
            "message_id": 232,
            "reply_to_msg_id": 231,
            "text": "【元婴状态】\n元婴正在温养。",
        }
        with patch.object(runtime, "should_pause_for_bot_health", return_value=False), \
             patch.object(runtime, "find_message_log_replies", return_value=[recovered_reply]) as recover_mock, \
             patch.object(runtime, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(runtime, "send_audit_log", new=AsyncMock()):
            asyncio.run(runtime.run_retry_scheduler(now, send_as_id=send_as_id))

        recover_mock.assert_called_once()
        send_mock.assert_not_awaited()
        with state_module.use_identity(send_as_id) as identity_state:
            self.assertEqual({}, identity_state["pending_tasks"])

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

    def test_pending_timeout_without_bot_seen_waits_for_global_silence_threshold(self):
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
        self.assertEqual(runtime.BOT_HEALTH_HEALTHY, runtime.get_bot_health_snapshot()["state"])
        with state_module.use_identity(send_as_id) as identity_state:
            self.assertIn(261, identity_state["pending_tasks"])

    def test_pending_timeout_marks_suspect_after_global_silence_threshold(self):
        send_as_id = 971009
        now = 7000.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                281: {
                    "cmd": ".灵树状态",
                    "sent_at": now - runtime.BOT_SILENCE_TIMEOUT_SEC - 1,
                    "retry": 0,
                    "timeout": 10,
                    "reply_to_msg_id": 0,
                    "priority": "normal",
                }
            }

        with patch.object(runtime, "send_game_command", new=AsyncMock()) as send_mock, \
             patch.object(runtime, "send_audit_log", new=AsyncMock()):
            runtime._bot_last_seen_at = now - runtime.BOT_SILENCE_TIMEOUT_SEC - 30
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

    def test_tianxing_craft_zero_retry_timeout_closes_action_guard_session(self):
        send_as_id = 971012
        now = 8500.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                406: {
                    "cmd": f"{config.CMD_CRAFT} 玄铁剑",
                    "sent_at": now - 400,
                    "retry": 0,
                    "timeout": 300,
                    "reply_to_msg_id": 0,
                    "priority": "normal",
                    "max_retry": 0,
                    "source_module": "天星宗",
                }
            }
            identity_state["action_guard_sessions"] = {
                "tianxing_craft_farm": {
                    "action_key": "tianxing_craft_farm",
                    "attempt": 1,
                    "last_sent_at": now - 400,
                    "last_msg_id": 406,
                    "last_command": f"{config.CMD_CRAFT} 玄铁剑",
                    "next_allowed_at": now + 600,
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
            self.assertNotIn("tianxing_craft_farm", identity_state["action_guard_sessions"])

    def test_tianxing_pending_with_default_retry_is_module_managed_without_resend(self):
        send_as_id = 971015
        now = 8600.0
        state_module.ensure_identity_registered(send_as_id)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["pending_tasks"] = {
                407: {
                    "cmd": config.CMD_TIANXING_PANEL,
                    "sent_at": now - 200,
                    "retry": 0,
                    "timeout": 120,
                    "reply_to_msg_id": 0,
                    "priority": "reactive",
                    "max_retry": 1,
                    "source_module": "天星宗",
                    "op_id": "wild-training-panel-calibration-test",
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
