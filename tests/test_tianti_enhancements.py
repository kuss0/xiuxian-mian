import atexit
import asyncio
import copy
import os
import sys
import unittest
from datetime import datetime
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
from model.features import passive_inbox, tianti


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

            self.assertIn("今日到不了第12阶", text)
            self.assertIn("最后一次登阶", text)

    def test_wenxin_waits_for_final_stage_when_reachable_today(self):
        send_as_id = 95004
        now = 4000.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tianti_wenxin_enabled"] = True
            state_module.state["tianti_progress_current"] = 10
            state_module.state["tianti_progress_total"] = 12
            state_module.state["tianti_remaining_climb_count"] = 2
            state_module.state["tianti_theoretical_max_stage"] = 12
            state_module.state["next_tianti_climb_time"] = now + 300

            should_trigger, reason = tianti._should_trigger_tianti_wenxin(now)

            self.assertFalse(should_trigger)
            self.assertEqual("wait_final_stage", reason)

    def test_wenxin_triggers_before_final_stage_climb(self):
        send_as_id = 95005
        now = 5000.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tianti_wenxin_enabled"] = True
            state_module.state["tianti_progress_current"] = 11
            state_module.state["tianti_progress_total"] = 12
            state_module.state["tianti_remaining_climb_count"] = 1
            state_module.state["tianti_theoretical_max_stage"] = 12
            state_module.state["next_tianti_climb_time"] = now + 300

            should_trigger, reason = tianti._should_trigger_tianti_wenxin(now)

            self.assertTrue(should_trigger)
            self.assertIn("final_stage", reason)

    def test_wenxin_triggers_on_last_climb_when_final_unreachable(self):
        send_as_id = 95006
        now = 6000.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tianti_wenxin_enabled"] = True
            state_module.state["tianti_progress_current"] = 10
            state_module.state["tianti_progress_total"] = 12
            state_module.state["tianti_remaining_climb_count"] = 1
            state_module.state["tianti_theoretical_max_stage"] = 11
            state_module.state["next_tianti_climb_time"] = now + 300

            should_trigger, reason = tianti._should_trigger_tianti_wenxin(now)

            self.assertTrue(should_trigger)
            self.assertIn("last_climb_today", reason)

    def test_wenxin_day_end_fallback_when_no_climb_left(self):
        send_as_id = 95007
        now = datetime(2026, 5, 12, 23, 30, tzinfo=tianti.TZ_LOCAL).timestamp()
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tianti_wenxin_enabled"] = True
            state_module.state["tianti_remaining_climb_count"] = 0

            should_trigger, reason = tianti._should_trigger_tianti_wenxin(now)

            self.assertTrue(should_trigger)
            self.assertIn("day_end_fallback", reason)

    def test_wenxin_day_end_fallback_does_not_repeat_after_send(self):
        send_as_id = 95008
        now = datetime(2026, 5, 12, 23, 30, tzinfo=tianti.TZ_LOCAL).timestamp()
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tianti_wenxin_enabled"] = True
            state_module.state["tianti_remaining_climb_count"] = 0
            state_module.state["tianti_wenxin_last_trigger_key"] = "2026-05-12|day_end_fallback"

            should_trigger, reason = tianti._should_trigger_tianti_wenxin(now)

            self.assertFalse(should_trigger)
            self.assertEqual("trigger_key_hit", reason)

    def test_wenxin_ignores_stale_24h_timer_after_day_change(self):
        send_as_id = 95009
        now = datetime(2026, 5, 12, 8, 0, tzinfo=tianti.TZ_LOCAL).timestamp()
        stale_next = datetime(2026, 5, 12, 12, 0, tzinfo=tianti.TZ_LOCAL).timestamp()
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tianti_wenxin_enabled"] = True
            state_module.state["tianti_progress_current"] = 11
            state_module.state["tianti_progress_total"] = 12
            state_module.state["tianti_remaining_climb_count"] = 1
            state_module.state["tianti_theoretical_max_stage"] = 12
            state_module.state["next_tianti_climb_time"] = now + 300
            state_module.state["next_tianti_wenxin_time"] = stale_next

            should_trigger, reason = tianti._should_trigger_tianti_wenxin(now)

            self.assertTrue(should_trigger)
            self.assertIn("final_stage", reason)

    def test_wenxin_trigger_key_tolerates_next_climb_second_jitter(self):
        send_as_id = 95017
        now = datetime(2026, 5, 27, 19, 29, 25, tzinfo=tianti.TZ_LOCAL).timestamp()
        old_next_climb = datetime(2026, 5, 27, 19, 37, 54, tzinfo=tianti.TZ_LOCAL).timestamp()
        jittered_next_climb = datetime(2026, 5, 27, 19, 37, 56, tzinfo=tianti.TZ_LOCAL).timestamp()
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tianti_wenxin_enabled"] = True
            state_module.state["tianti_progress_current"] = 11
            state_module.state["tianti_progress_total"] = 12
            state_module.state["tianti_remaining_climb_count"] = 1
            state_module.state["tianti_theoretical_max_stage"] = 12
            state_module.state["next_tianti_climb_time"] = jittered_next_climb
            state_module.state["tianti_wenxin_last_trigger_key"] = f"{tianti.get_day_key(now)}|11|12|{int(old_next_climb)}|final_stage"

            should_trigger, reason = tianti._should_trigger_tianti_wenxin(now)

            self.assertFalse(should_trigger)
            self.assertEqual("trigger_key_hit", reason)

    def test_wenxin_send_sets_short_inflight_gate_until_reply(self):
        send_as_id = 95018
        now = datetime(2026, 5, 27, 19, 29, 2, tzinfo=tianti.TZ_LOCAL).timestamp()
        next_climb = datetime(2026, 5, 27, 19, 37, 54, tzinfo=tianti.TZ_LOCAL).timestamp()
        sent_at = now + 0.5
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id), \
                patch.object(tianti, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9466030, sent_at=sent_at))), \
                patch.object(tianti, "save_state") as save_mock, \
                patch.object(tianti, "console_log"):
            state_module.state["tianti_enabled"] = True
            state_module.state["tianti_wenxin_enabled"] = True
            state_module.state["tianti_gangfeng_enabled"] = False
            state_module.state["tianti_progress_current"] = 11
            state_module.state["tianti_progress_total"] = 12
            state_module.state["tianti_remaining_climb_count"] = 1
            state_module.state["tianti_theoretical_max_stage"] = 12
            state_module.state["next_tianti_climb_time"] = next_climb

            asyncio.run(tianti.run_tianti_scheduler(now))

            self.assertEqual(9466030, state_module.state["tianti_last_wenxin_msg_id"])
            self.assertEqual(sent_at + tianti.TIANTI_WENXIN_INFLIGHT_GATE_SEC, state_module.state["next_tianti_wenxin_time"])
            self.assertTrue(str(state_module.state["tianti_wenxin_last_trigger_key"]).startswith(tianti.get_day_key(now)))
            save_mock.assert_called()

    def test_stale_status_snapshot_syncs_before_gangfeng(self):
        send_as_id = 95010
        now = 10_000.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tianti_gangfeng_enabled"] = True
            state_module.state["tianti_progress_current"] = 5
            state_module.state["tianti_cycle_count"] = 1
            state_module.state["tianti_gangfeng_level"] = 2
            state_module.state["tianti_cooldown_text"] = "可立即登阶"
            state_module.state["next_tianti_climb_time"] = now + 300
            state_module.state["next_tianti_gangfeng_time"] = now - 1
            state_module.state["tianti_last_status_seen_at"] = now - tianti.TIANTI_STATUS_FRESH_SEC - 1

            self.assertTrue(tianti._tianti_status_sync_due(now))
            should_trigger, reason = tianti._should_trigger_tianti_gangfeng(now)

            self.assertFalse(should_trigger)
            self.assertEqual("gangfeng_status_stale", reason)

    def test_fresh_status_snapshot_allows_due_gangfeng(self):
        send_as_id = 95011
        now = 11_000.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tianti_gangfeng_enabled"] = True
            state_module.state["tianti_progress_current"] = 5
            state_module.state["tianti_cycle_count"] = 1
            state_module.state["tianti_gangfeng_level"] = 2
            state_module.state["tianti_cooldown_text"] = "可立即登阶"
            state_module.state["next_tianti_climb_time"] = now + 300
            state_module.state["next_tianti_gangfeng_time"] = now - 1
            state_module.state["tianti_last_status_seen_at"] = now

            should_trigger, reason = tianti._should_trigger_tianti_gangfeng(now)

            self.assertTrue(should_trigger)
            self.assertIn("bucket=", reason)

    def test_gangfeng_trigger_key_tolerates_next_climb_second_jitter(self):
        send_as_id = 95015
        now = datetime(2026, 5, 27, 19, 29, 25, tzinfo=tianti.TZ_LOCAL).timestamp()
        old_next_climb = datetime(2026, 5, 27, 19, 37, 54, tzinfo=tianti.TZ_LOCAL).timestamp()
        jittered_next_climb = datetime(2026, 5, 27, 19, 37, 56, tzinfo=tianti.TZ_LOCAL).timestamp()
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id):
            state_module.state["tianti_gangfeng_enabled"] = True
            state_module.state["tianti_progress_current"] = 2
            state_module.state["tianti_cycle_count"] = 26
            state_module.state["tianti_gangfeng_level"] = 12
            state_module.state["tianti_cooldown_text"] = "9分钟21秒"
            state_module.state["next_tianti_climb_time"] = jittered_next_climb
            state_module.state["next_tianti_gangfeng_time"] = 0
            state_module.state["tianti_last_status_seen_at"] = now
            state_module.state["tianti_gangfeng_last_trigger_key"] = f"{tianti.get_day_key(now)}|{int(old_next_climb)}"

            should_trigger, reason = tianti._should_trigger_tianti_gangfeng(now)

            self.assertFalse(should_trigger)
            self.assertEqual("gangfeng_trigger_key_hit", reason)

    def test_gangfeng_send_sets_short_inflight_gate_until_reply(self):
        send_as_id = 95016
        now = datetime(2026, 5, 27, 19, 29, 2, tzinfo=tianti.TZ_LOCAL).timestamp()
        next_climb = datetime(2026, 5, 27, 19, 37, 54, tzinfo=tianti.TZ_LOCAL).timestamp()
        sent_at = now + 0.5
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id), \
                patch.object(tianti, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=9466041, sent_at=sent_at))), \
                patch.object(tianti, "save_state") as save_mock, \
                patch.object(tianti, "console_log"):
            state_module.state["tianti_enabled"] = True
            state_module.state["tianti_wenxin_enabled"] = False
            state_module.state["tianti_gangfeng_enabled"] = True
            state_module.state["tianti_progress_current"] = 2
            state_module.state["tianti_cycle_count"] = 26
            state_module.state["tianti_gangfeng_level"] = 12
            state_module.state["tianti_cooldown_text"] = "9分钟21秒"
            state_module.state["next_tianti_climb_time"] = next_climb
            state_module.state["next_tianti_gangfeng_time"] = 0
            state_module.state["tianti_last_status_seen_at"] = now

            asyncio.run(tianti.run_tianti_scheduler(now))

            self.assertEqual(9466041, state_module.state["tianti_last_gangfeng_msg_id"])
            self.assertEqual(sent_at + tianti.TIANTI_GANGFENG_INFLIGHT_GATE_SEC, state_module.state["next_tianti_gangfeng_time"])
            self.assertEqual("等待回复", state_module.state["tianti_gangfeng_status"])
            self.assertTrue(str(state_module.state["tianti_gangfeng_last_trigger_key"]).startswith(tianti.get_day_key(now)))
            save_mock.assert_called()

    def test_climb_reply_with_gangfeng_wait_updates_climb_gate(self):
        send_as_id = 95012
        now = 12_000.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id), \
                patch.object(tianti.random, "randint", return_value=0), \
                patch.object(tianti, "send_audit_log", new=AsyncMock()), \
                patch.object(tianti, "save_state"):
            state_module.state["tianti_enabled"] = True
            state_module.state["next_tianti_climb_time"] = now - 1
            state_module.state["next_tianti_gangfeng_time"] = now - 1
            reply_to = SimpleNamespace(id=9387264, raw_text=".登天阶")

            handled = asyncio.run(tianti.handle_tianti_reply(
                "九天罡风尚未再聚，请在 17秒 后再试。",
                now,
                reply_to,
                matched_family="tianti_climb",
            ))

            self.assertTrue(handled)
            self.assertEqual(now + 17, state_module.state["next_tianti_climb_time"])
            self.assertEqual(now + 17, state_module.state["next_tianti_gangfeng_time"])
            self.assertEqual(9387264, state_module.state["tianti_last_climb_msg_id"])
            self.assertEqual("", state_module.state["tianti_last_error"])
            self.assertEqual(0, state_module.state.get("tianti_last_gain_xiuwei", 0))

    def test_gangfeng_reply_accepts_short_wait_wording(self):
        send_as_id = 95013
        now = 13_000.0
        state_module.ensure_identity_registered(send_as_id)

        with state_module.use_identity(send_as_id), \
                patch.object(tianti.random, "randint", return_value=0), \
                patch.object(tianti, "send_audit_log", new=AsyncMock()), \
                patch.object(tianti, "save_state"):
            state_module.state["tianti_enabled"] = True
            state_module.state["next_tianti_climb_time"] = now + 300
            state_module.state["next_tianti_gangfeng_time"] = now - 1
            reply_to = SimpleNamespace(id=9387751, raw_text=".引九天罡风")

            handled = asyncio.run(tianti.handle_tianti_reply(
                "九天罡风尚未再聚，请在 58分钟55秒 后再试。",
                now,
                reply_to,
                matched_family="tianti_gangfeng",
            ))

            self.assertTrue(handled)
            self.assertEqual(now + 58 * 60 + 55, state_module.state["next_tianti_gangfeng_time"])
            self.assertEqual(now + 300, state_module.state["next_tianti_climb_time"])
            self.assertEqual(9387751, state_module.state["tianti_last_gangfeng_msg_id"])

    def test_passive_wenxin_reply_does_not_replay_active_closeout(self):
        send_as_id = 95014
        now = datetime(2026, 5, 27, 10, 26, 38, tzinfo=tianti.TZ_LOCAL).timestamp()
        state_module.ensure_identity_registered(send_as_id)
        text = (
            "【问心台回响】\n"
            "你于问心台前静坐良久，最终凝出一道【澄明】之印。\n"
            "下次登天阶时，成功率显著提升。\n\n"
            "你因此获得了 20 点宗门贡献。"
        )
        reply_to = SimpleNamespace(id=9447960, raw_text=".问心台")

        with state_module.use_identity(send_as_id), \
                patch.object(tianti, "send_audit_log", new=AsyncMock()), \
                patch.object(tianti, "console_log") as active_log, \
                patch.object(tianti, "save_state"):
            state_module.state["tianti_enabled"] = True
            active_handled = asyncio.run(tianti.handle_tianti_reply(
                text,
                now,
                reply_to,
                matched_family="tianti_wenxin",
            ))

            self.assertTrue(active_handled)
            active_log.assert_any_call("☁️ 问心收口：成功，下次登天阶奖励提升")
            self.assertEqual("今日已问心，下次登天阶奖励提升", state_module.state["tianti_wenxin_status"])
            self.assertEqual(9447960, state_module.state["tianti_last_wenxin_msg_id"])

        with state_module.use_identity(send_as_id), \
                patch.object(tianti, "console_log") as passive_log, \
                patch.object(passive_inbox, "_save_passive_stats"), \
                patch.object(passive_inbox, "save_state"):
            passive_handled = asyncio.run(passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={"send_as_id": send_as_id, "family": "tianti_wenxin"},
            ))

            self.assertFalse(passive_handled)
            passive_log.assert_not_called()
            self.assertEqual("今日已问心，下次登天阶奖励提升", state_module.state["tianti_wenxin_status"])
            self.assertEqual(9447960, state_module.state["tianti_last_wenxin_msg_id"])


if __name__ == "__main__":
    unittest.main()
