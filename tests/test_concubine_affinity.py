import atexit
import copy
import json
import sys
import tempfile
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

from model import config
from model import runtime
from model import state as state_module
from model.features import concubine, passive_inbox, workflow_log


def _read_workflow_events(tmpdir):
    events = []
    for path in Path(tmpdir).glob("**/*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


class ConcubineAffinityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._passive_stats_snapshot = copy.deepcopy(passive_inbox._passive_stats)
        self._observed_passive_snapshot = dict(passive_inbox._observed_passive_events)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        passive_inbox._passive_stats = {
            "total": 0,
            "changed": 0,
            "skipped": 0,
            "modules": {},
            "skip_reasons": {},
            "recent": [],
        }
        passive_inbox._observed_passive_events = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        passive_inbox._passive_stats = self._passive_stats_snapshot
        passive_inbox._observed_passive_events = self._observed_passive_snapshot

    def _prepare_identity(self, *, affinity=1000, dream_due_at=1_700_000_600.0, tianji_due_at=1_699_999_000.0, sect_name="星宫", kind="道心侍妾"):
        send_as_id = 991101
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="xinggong", sect_name=sect_name)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = True
            identity_state["concubine_tianji_enabled"] = True
            identity_state["concubine_heart_enabled"] = False
            identity_state["concubine_phase"] = "idle"
            identity_state["concubine_availability"] = "available"
            identity_state["concubine_name"] = "凌玉灵"
            identity_state["concubine_kind"] = kind
            identity_state["concubine_affinity"] = affinity
            identity_state["concubine_dream_due_at"] = dream_due_at
            identity_state["concubine_tianji_due_at"] = tianji_due_at
            identity_state["next_concubine_time"] = 0
        return send_as_id

    def _inbox_summaries(self, inbox_mock):
        return [str(call.kwargs.get("summary") or "") for call in inbox_mock.call_args_list]

    def _log_ts(self, ts):
        return datetime.fromtimestamp(float(ts), config.TZ_LOCAL).strftime("%Y-%m-%d %H:%M:%S UTC+8")

    def _write_message_log(self, log_dir, entries, now):
        day = datetime.fromtimestamp(float(now), config.TZ_LOCAL).date().isoformat()
        log_path = Path(log_dir) / f"{day}.log"
        with log_path.open("w", encoding="utf-8") as f:
            for entry in entries:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return log_path

    def test_message_log_topic_guard_matches_real_log_shapes(self):
        with patch.object(concubine, "get_game_topic_id", return_value=7310786):
            self.assertTrue(concubine._payload_matches_game_topic({"reply_to_msg_id": 9796379}))
            self.assertTrue(concubine._payload_matches_game_topic({"topic_id": 7310786, "reply_to_msg_id": 9796379}))
            self.assertTrue(concubine._payload_matches_game_topic({"topic_id": 0, "reply_to_msg_id": 7310786}))
            self.assertFalse(concubine._payload_matches_game_topic({"topic_id": 458347, "reply_to_msg_id": 9797504}))
            self.assertFalse(concubine._payload_matches_game_topic({"topic_id": 0, "reply_to_msg_id": 458347}))

    async def test_selfless_realm_marks_affinity_zero_and_schedules_recovery(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        text = (
            "【无我之境】\n"
            "在你心神即将被心魔吞噬的危急时刻，侍妾 凌玉灵 挺身而出，"
            "耗尽与你的所有情缘为你挡下此劫...\n"
            "你成功渡过此劫，修为未损。"
        )

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_affinity_event(text, now, SimpleNamespace(id=1))
            self.assertTrue(handled)
            self.assertEqual(0, state_module.state["concubine_affinity"])
            self.assertIn("无我之境耗尽情缘", state_module.state["concubine_tianji_last_error"])
            self.assertGreater(state_module.state["concubine_tianji_due_at"], now)
            self.assertEqual(now, state_module.state["next_concubine_time"])

    async def test_tianji_low_affinity_reply_overrides_stale_high_affinity(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_TIANJI, id=123)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "tianji_pending"
            identity_state["concubine_tianji_msg_id"] = 123

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_tianji_reply(
                "你与侍妾情缘未至，至少需 300 情缘方可代卜天机。",
                now,
                reply_to,
                matched_family="concubine_tianji",
            )
            self.assertTrue(handled)
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(0, state_module.state["concubine_affinity"])
            self.assertGreater(state_module.state["concubine_tianji_due_at"], now)
            self.assertEqual(now, state_module.state["next_concubine_time"])

    async def test_tianji_short_cooldown_reply_uses_real_wait_wording(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(dream_due_at=now + 3600, tianji_due_at=now - 1)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_TIANJI, id=124)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "tianji_pending"
            identity_state["concubine_tianji_msg_id"] = 124
            identity_state["concubine_tianji_last_error"] = "pending"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_tianji_reply(
                "天机链路尚未重铸，请在 24 秒后再试。",
                now,
                reply_to,
                matched_family="concubine_tianji",
            )

        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_tianji_msg_id"])
        self.assertEqual("", state_module.state["concubine_tianji_last_error"])
        self.assertEqual(now + 24 + config.CD_BUFFER_SEC, state_module.state["concubine_tianji_due_at"])
        self.assertEqual(state_module.state["concubine_tianji_due_at"], state_module.state["next_concubine_time"])

    async def test_affinity_gain_clears_tianji_block_after_threshold(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=270, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_last_error"] = "情缘恢复中（270/300），暂缓天机代卜"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await concubine.handle_concubine_affinity_event(
                "侍妾【凌玉灵】向你微微颔首，你们的情缘增加了 30 点。",
                now,
                SimpleNamespace(id=2),
            )
            self.assertTrue(handled)
            self.assertEqual(300, state_module.state["concubine_affinity"])
            self.assertEqual("", state_module.state["concubine_tianji_last_error"])
            self.assertEqual(now + 30, state_module.state["next_concubine_time"])

    async def test_affinity_fallback_requires_identity_hint_before_name_match(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=270, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_last_error"] = "情缘恢复中（270/300），暂缓天机代卜"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state") as mock_save:
            handled = await concubine.handle_concubine_affinity_event(
                "侍妾【凌玉灵】向你微微颔首，你们的情缘增加了 30 点。",
                now,
                SimpleNamespace(id=3),
                require_identity_hint=True,
            )

        self.assertFalse(handled)
        mock_save.assert_not_called()
        self.assertEqual(270, state_module.state["concubine_affinity"])
        self.assertEqual("情缘恢复中（270/300），暂缓天机代卜", state_module.state["concubine_tianji_last_error"])

    async def test_affinity_fallback_accepts_explicit_identity_hint(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=270, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_last_error"] = "情缘恢复中（270/300），暂缓天机代卜"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await concubine.handle_concubine_affinity_event(
                "@xinggong 侍妾【凌玉灵】向你微微颔首，你们的情缘增加了 30 点。",
                now,
                SimpleNamespace(id=4),
                require_identity_hint=True,
            )

        self.assertTrue(handled)
        self.assertEqual(300, state_module.state["concubine_affinity"])
        self.assertEqual("", state_module.state["concubine_tianji_last_error"])

    async def test_scheduler_sends_daily_greet_only_when_affinity_below_threshold(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=270, dream_due_at=now + 3600, tianji_due_at=now - 1)
        sent_msg = SimpleNamespace(id=456, sent_at=now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_awaited_once_with(config.CMD_CONCUBINE_DAILY_GREET, track=False)
        self.assertEqual("greet_pending", state_module.state["concubine_phase"])
        self.assertEqual(456, state_module.state["concubine_greet_msg_id"])

    async def test_scheduler_does_not_greet_when_affinity_reaches_threshold(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=300, dream_due_at=now + 3600, tianji_due_at=now + 600)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
             patch.object(concubine.random, "uniform", return_value=30):
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual("idle", state_module.state["concubine_phase"])

    async def test_scheduler_respects_future_next_time_even_if_active_due_is_stale(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["next_concubine_time"] = now + 300
            identity_state["concubine_last_snapshot_at"] = now - 3600

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()

    async def test_scheduler_calibrates_before_dream_when_snapshot_is_stale(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_snapshot_at"] = now - 24 * 3600

        sent_msg = SimpleNamespace(id=987, sent_at=now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_awaited_once_with(config.CMD_CONCUBINE_STATUS, track=False)
        self.assertEqual("status_pending", state_module.state["concubine_phase"])
        self.assertEqual(987, state_module.state["concubine_status_msg_id"])
        self.assertEqual(0, state_module.state["concubine_dream_msg_id"])
        self.assertIn("主动动作前状态校准", state_module.state["concubine_last_error"])

    async def test_scheduler_calibrates_before_tianji_when_snapshot_is_stale(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_panel_msg_id"] = 123
            identity_state["concubine_last_snapshot_at"] = now - 24 * 3600

        sent_msg = SimpleNamespace(id=989, sent_at=now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_awaited_once_with(config.CMD_CONCUBINE_STATUS, track=False)
        self.assertEqual("status_pending", state_module.state["concubine_phase"])
        self.assertEqual(989, state_module.state["concubine_status_msg_id"])
        self.assertEqual(0, state_module.state["concubine_tianji_msg_id"])
        self.assertIn("主动动作前状态校准", state_module.state["concubine_last_error"])

    async def test_scheduler_allows_tianji_when_snapshot_is_fresh(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_panel_msg_id"] = 123
            identity_state["concubine_last_snapshot_at"] = now

        sent_msg = SimpleNamespace(id=990, sent_at=now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_awaited_once_with(config.CMD_CONCUBINE_TIANJI, track=False)
        self.assertEqual("tianji_pending", state_module.state["concubine_phase"])
        self.assertEqual(990, state_module.state["concubine_tianji_msg_id"])

    async def test_scheduler_blocks_tianji_when_recent_success_log_shows_future_cooldown(self):
        now = 1_700_000_000.0
        sent_at = now - 3 * 3600
        reply_at = sent_at + 2
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_due_at"] = now - 1
            identity_state["next_concubine_time"] = now - 1

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(
                tmpdir,
                [
                    {
                        "ts": self._log_ts(sent_at),
                        "event_type": "sent",
                        "message_id": 1001,
                        "sender_id": send_as_id,
                        "text": config.CMD_CONCUBINE_TIANJI,
                    },
                    {
                        "ts": self._log_ts(reply_at),
                        "event_type": "message",
                        "message_id": 1002,
                        "reply_to_msg_id": 1001,
                        "text": "【天机代卜链】\n得卦【残图引路】：下一次 .入梦寻图 的残图片段掉率大幅提升。",
                    },
                ],
                now,
            )
            with state_module.use_identity(send_as_id), \
                 patch.object(concubine, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
                 patch.object(concubine.random, "uniform", return_value=30):
                await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        expected_due = reply_at + config.CONCUBINE_TIANJI_CD_SEC + config.CD_BUFFER_SEC
        self.assertEqual(expected_due, state_module.state["concubine_tianji_due_at"])
        self.assertEqual("残图引路", state_module.state["concubine_tianji_chain"])
        self.assertEqual(expected_due, state_module.state["concubine_tianji_chain_due_at"])
        self.assertEqual(expected_due + 30, state_module.state["next_concubine_time"])

    async def test_scheduler_blocks_tianji_when_recent_cooldown_log_shows_future_wait(self):
        now = 1_700_000_000.0
        sent_at = now - 60
        reply_at = sent_at + 2
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_due_at"] = now - 1
            identity_state["next_concubine_time"] = now - 1

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(
                tmpdir,
                [
                    {
                        "ts": self._log_ts(sent_at),
                        "event_type": "sent",
                        "message_id": 1003,
                        "sender_id": send_as_id,
                        "text": config.CMD_CONCUBINE_TIANJI,
                    },
                    {
                        "ts": self._log_ts(reply_at),
                        "event_type": "message",
                        "message_id": 1004,
                        "reply_to_msg_id": 1003,
                        "text": "天机链路尚未重铸，请在 9小时5分钟31秒 后再试。",
                    },
                ],
                now,
            )
            with state_module.use_identity(send_as_id), \
                 patch.object(concubine, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
                 patch.object(concubine.random, "uniform", return_value=30):
                await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        expected_due = reply_at + 9 * 3600 + 5 * 60 + 31 + config.CD_BUFFER_SEC
        self.assertEqual(expected_due, state_module.state["concubine_tianji_due_at"])
        self.assertEqual(expected_due + 30, state_module.state["next_concubine_time"])

    async def test_scheduler_refreshes_status_when_heart_panel_is_stale(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_heart_due_at"] = now - 1
            identity_state["concubine_last_panel_msg_id"] = 123
            identity_state["concubine_last_snapshot_at"] = now - concubine.CONCUBINE_HEART_PANEL_MAX_AGE_SEC - 1

        sent_msg = SimpleNamespace(id=988, sent_at=now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_awaited_once_with(config.CMD_CONCUBINE_STATUS, track=False)
        self.assertEqual("status_pending", state_module.state["concubine_phase"])
        self.assertEqual(988, state_module.state["concubine_status_msg_id"])

    async def test_scheduler_defers_dream_command_during_phaseful_summary_window(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_snapshot_at"] = now - 24 * 3600
            identity_state["deep_retreat_enabled"] = True
            identity_state["deep_retreat_phase"] = "summary_due"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
             patch.object(concubine.random, "uniform", return_value=90):
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_status_msg_id"])
        self.assertEqual(now + 90, state_module.state["next_concubine_time"])
        self.assertIn("入梦寻图等待闭关/元婴结算", state_module.state["concubine_last_error"])

    async def test_scheduler_defers_tianji_command_during_phaseful_summary_window(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_snapshot_at"] = now
            identity_state["deep_retreat_enabled"] = True
            identity_state["deep_retreat_phase"] = "waiting_summary"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
             patch.object(concubine.random, "uniform", return_value=90):
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(now + 90, state_module.state["next_concubine_time"])
        self.assertIn("天机代卜等待闭关/元婴结算", state_module.state["concubine_tianji_last_error"])

    async def test_dream_reply_keeps_due_tianji_on_short_chain(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 321

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await concubine.handle_concubine_dream_reply(
                "【入梦寻图】\n本次梦兆锁定：【虚天残图】 线路。\n你与侍妾【凌玉灵】共梦乱星海，获得 【虚天残图】 残纹 北阙残纹（新残纹）。\n当前进度：1/4。",
                now,
                SimpleNamespace(raw_text=config.CMD_CONCUBINE_DREAM, id=321),
                matched_family="concubine_dream",
            )

        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(now + 30, state_module.state["next_concubine_time"])
        self.assertLessEqual(state_module.state["concubine_tianji_due_at"], now)

    async def test_daily_greet_reply_marks_day_and_clears_block_at_threshold(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=270, dream_due_at=now + 3600, tianji_due_at=now - 1)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_DAILY_GREET, id=456)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "greet_pending"
            identity_state["concubine_greet_msg_id"] = 456
            identity_state["concubine_tianji_last_error"] = "情缘恢复中（270/300），暂缓天机代卜"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await concubine.handle_concubine_greet_reply(
                "侍妾【凌玉灵】向你微微颔首，你们的情缘增加了 30 点。",
                now,
                reply_to,
                matched_family="concubine_greet",
            )

        self.assertTrue(handled)
        self.assertEqual(300, state_module.state["concubine_affinity"])
        self.assertEqual("", state_module.state["concubine_tianji_last_error"])
        self.assertEqual("", state_module.state["concubine_greet_last_error"])
        self.assertEqual(concubine._local_day_key(now), state_module.state["concubine_last_greet_day"])
        self.assertEqual(0, state_module.state["concubine_greet_msg_id"])
        self.assertEqual(0, state_module.state["concubine_greet_retry_count"])
        self.assertEqual("idle", state_module.state["concubine_phase"])

    async def test_daily_greet_repeat_reply_prevents_same_day_resend(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=120, dream_due_at=now + 3600, tianji_due_at=now - 1)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_DAILY_GREET, id=456)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "greet_pending"
            identity_state["concubine_greet_msg_id"] = 456

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_greet_reply(
                "今日已经问安过了，请勿过多打扰。你的心意她已收到。",
                now,
                reply_to,
                matched_family="concubine_greet",
            )
        self.assertTrue(handled)
        self.assertEqual(concubine._local_day_key(now), state_module.state["concubine_last_greet_day"])
        self.assertEqual(0, state_module.state["concubine_greet_retry_count"])

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=789, sent_at=now + 60))) as mock_send:
            await concubine.run_concubine_scheduler(now + 60)
        mock_send.assert_awaited_once_with(config.CMD_CONCUBINE_STATUS, track=False)
        self.assertEqual("gift_status_pending", state_module.state["concubine_phase"])

    async def test_scheduler_after_daily_greet_requests_status_for_gift_recovery(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_greet_day"] = concubine._local_day_key(now)

        sent_msg = SimpleNamespace(id=501, sent_at=now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_awaited_once_with(config.CMD_CONCUBINE_STATUS, track=False)
        self.assertEqual("gift_status_pending", state_module.state["concubine_phase"])
        self.assertEqual(501, state_module.state["concubine_gift_status_msg_id"])
        self.assertEqual(concubine._local_day_key(now), state_module.state["concubine_gift_attempt_day"])

    async def test_gift_attempt_day_blocks_duplicate_recovery_chain_start(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        today = concubine._local_day_key(now)
        sent_msg = SimpleNamespace(id=501, sent_at=now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_greet_day"] = today

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_awaited_once_with(config.CMD_CONCUBINE_STATUS, track=False)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "idle"
            identity_state["concubine_gift_status_msg_id"] = 0
            identity_state["next_concubine_time"] = now

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(now + 1)

        mock_send.assert_not_awaited()
        self.assertEqual(today, state_module.state["concubine_gift_attempt_day"])

    def test_concubine_persisted_message_ids_route_to_reply_families(self):
        state = state_module.new_identity_state()
        state["concubine_gift_status_msg_id"] = 501
        state["concubine_gift_bag_msg_id"] = 601
        state["concubine_gift_msg_id"] = 701
        state["concubine_tianji_msg_id"] = 801
        state["concubine_voyage_msg_id"] = 901

        self.assertEqual("concubine_status", runtime._get_special_tracked_message_family(state, 501))
        self.assertEqual("storage_bag", runtime._get_special_tracked_message_family(state, 601))
        self.assertEqual("concubine_gift", runtime._get_special_tracked_message_family(state, 701))
        self.assertEqual("concubine_tianji", runtime._get_special_tracked_message_family(state, 801))
        self.assertEqual("concubine_voyage", runtime._get_special_tracked_message_family(state, 901))

    async def test_gift_status_and_bag_reply_sends_exact_stone_amount(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        status_text = (
            "你的道心侍妾: 【凌玉灵】 (状态: 随行中)\n"
            "情缘值: 240\n"
            "当前誓约: 无\n"
            "命令: .每日问安、.天机代卜"
        )
        bag_text = (
            "@xinggong 的储物袋\n"
            "法宝/丹药/杂物:\n"
            "- 灵石 x 1,000\n"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "gift_status_pending"
            identity_state["concubine_gift_status_msg_id"] = 501
            identity_state["concubine_last_greet_day"] = concubine._local_day_key(now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=601, sent_at=now))) as mock_send:
            handled = await concubine.handle_concubine_status_reply(
                status_text,
                now,
                SimpleNamespace(raw_text=config.CMD_CONCUBINE_STATUS, id=501),
                matched_family="concubine_status",
                current_msg_id=502,
            )
        self.assertTrue(handled)
        mock_send.assert_awaited_once_with(".储物袋", track=False)
        self.assertEqual("gift_bag_pending", state_module.state["concubine_phase"])
        self.assertEqual(601, state_module.state["concubine_gift_bag_msg_id"])

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=701, sent_at=now))) as mock_send:
            handled = await concubine.handle_concubine_storage_bag_reply(
                bag_text,
                now,
                SimpleNamespace(raw_text=".储物袋", id=601),
                matched_family="storage_bag",
            )
        self.assertTrue(handled)
        mock_send.assert_awaited_once_with(f"{config.CMD_CONCUBINE_GIFT_STONE} 灵石*60", track=False)
        self.assertEqual("gift_pending", state_module.state["concubine_phase"])
        self.assertEqual(60, state_module.state["concubine_gift_amount"])

    async def test_gift_bag_reply_continues_after_phase_was_cleared(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=60, dream_due_at=now + 3600, tianji_due_at=now - 1)
        bag_text = (
            "@xinggong 的储物袋\n"
            "材料:\n"
            "- 灵石 x 5,222\n"
        )
        today = concubine._local_day_key(now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "idle"
            identity_state["concubine_last_greet_day"] = today
            identity_state["concubine_gift_attempt_day"] = today
            identity_state["concubine_gift_status_msg_id"] = 0
            identity_state["concubine_gift_bag_msg_id"] = 0

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=701, sent_at=now))) as mock_send:
            handled = await concubine.handle_concubine_storage_bag_reply(
                bag_text,
                now,
                SimpleNamespace(raw_text=".储物袋", id=601),
                matched_family="storage_bag",
            )

        self.assertTrue(handled)
        mock_send.assert_awaited_once_with(f"{config.CMD_CONCUBINE_GIFT_STONE} 灵石*240", track=False)
        self.assertEqual("gift_pending", state_module.state["concubine_phase"])
        self.assertEqual(240, state_module.state["concubine_gift_amount"])

    async def test_gift_success_updates_affinity_and_unblocks_tianji(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "gift_pending"
            identity_state["concubine_gift_msg_id"] = 701
            identity_state["concubine_gift_amount"] = 60
            identity_state["concubine_last_greet_day"] = concubine._local_day_key(now)
            identity_state["concubine_tianji_last_error"] = "情缘恢复中（240/300），暂缓天机代卜"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "apply_storage_bag_item_deltas", return_value=True), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_gift_reply(
                "你将【灵石】x60 赠予了侍妾【凌玉灵】，你们的情缘增加了 60 点！",
                now,
                SimpleNamespace(raw_text=f"{config.CMD_CONCUBINE_GIFT_STONE} 灵石*60", id=701),
                matched_family="concubine_gift",
            )

        self.assertTrue(handled)
        self.assertEqual(300, state_module.state["concubine_affinity"])
        self.assertEqual("", state_module.state["concubine_tianji_last_error"])
        self.assertEqual(concubine._local_day_key(now), state_module.state["concubine_last_gift_day"])
        self.assertEqual(0, state_module.state["concubine_gift_msg_id"])
        self.assertEqual(0, state_module.state["concubine_gift_amount"])
        self.assertEqual("idle", state_module.state["concubine_phase"])

    async def test_gift_insufficient_stones_marks_day_and_does_not_send(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=240, dream_due_at=now + 3600, tianji_due_at=now - 1)
        bag_text = (
            "@xinggong 的储物袋\n"
            "材料:\n"
            "- 灵石 x 10\n"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "gift_bag_pending"
            identity_state["concubine_gift_bag_msg_id"] = 601
            identity_state["concubine_last_greet_day"] = concubine._local_day_key(now)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            handled = await concubine.handle_concubine_storage_bag_reply(
                bag_text,
                now,
                SimpleNamespace(raw_text=".储物袋", id=601),
                matched_family="storage_bag",
            )
        self.assertTrue(handled)
        mock_send.assert_not_awaited()
        self.assertEqual(concubine._local_day_key(now), state_module.state["concubine_last_gift_day"])
        self.assertIn("灵石不足", state_module.state["concubine_gift_last_error"])

    async def test_daily_greet_summary_trigger_schedules_single_retry_without_marking_day(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=0, dream_due_at=now + 3600, tianji_due_at=now - 1)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_DAILY_GREET, id=456)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "greet_pending"
            identity_state["concubine_greet_msg_id"] = 456

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=90):
            handled = await concubine.handle_concubine_greet_reply(
                "✨ 天道感应：检测到 @xinggong 功成圆满，神魂正在归位...",
                now,
                reply_to,
                matched_family="deep_retreat",
            )

        self.assertTrue(handled)
        self.assertEqual("", state_module.state["concubine_last_greet_day"])
        self.assertEqual(1, state_module.state["concubine_greet_retry_count"])
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_greet_msg_id"])
        self.assertEqual(now + 90, state_module.state["next_concubine_time"])
        self.assertIn("稍后补发", state_module.state["concubine_greet_last_error"])

    async def test_daily_greet_second_timeout_marks_day_to_avoid_storm(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=0, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "greet_pending"
            identity_state["concubine_greet_msg_id"] = 456
            identity_state["concubine_greet_retry_count"] = 1
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()), \
             patch.object(concubine.random, "uniform", return_value=0):
            await concubine.run_concubine_scheduler(now)

        self.assertEqual(concubine._local_day_key(now), state_module.state["concubine_last_greet_day"])
        self.assertEqual(0, state_module.state["concubine_greet_retry_count"])
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_greet_msg_id"])
        self.assertIn("今日不再补发", state_module.state["concubine_greet_last_error"])

    async def test_scheduler_defers_daily_greet_during_deep_retreat_summary_wait(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=120, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["deep_retreat_enabled"] = True
            identity_state["deep_retreat_phase"] = "summary_due"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
             patch.object(concubine.random, "uniform", return_value=90):
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertEqual(now + 90, state_module.state["next_concubine_time"])
        self.assertIn("等待闭关/元婴结算", state_module.state["concubine_greet_last_error"])

    async def test_non_star_palace_identity_does_not_daily_greet(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=120, dream_due_at=now + 3600, tianji_due_at=now - 1, sect_name="太一门")

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send, \
             patch.object(concubine.random, "uniform", return_value=0):
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertIn("情缘不足", state_module.state["concubine_tianji_last_error"])

    async def test_scheduler_clears_stale_affinity_error_when_threshold_is_met(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1010, dream_due_at=now + 3600, tianji_due_at=now + 600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_last_error"] = "情缘不足（0/300），暂缓天机代卜"

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            await concubine.run_concubine_scheduler(now)

        self.assertEqual(1010, state_module.state["concubine_affinity"])
        self.assertEqual("", state_module.state["concubine_tianji_last_error"])
        self.assertEqual(now + 630, state_module.state["next_concubine_time"])

    async def test_heart_prompt_keeps_guard_until_terminal_reply(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "heart_pending"
            identity_state["concubine_heart_msg_id"] = 10
            identity_state["action_guard_sessions"] = {
                "concubine_heart": {
                    "action_key": "concubine_heart",
                    "attempt": 1,
                    "next_allowed_at": now + 900,
                }
            }

        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_HEART, id=10)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=12):
            handled = await concubine.handle_concubine_heart_reply(
                "【坠魔心劫·第一轮】\n请回复本消息 .稳 / .狠 / .骗 进行抉择（共3轮）。",
                now,
                reply_to,
                matched_family="concubine_heart",
                current_msg_id=20,
            )
            self.assertTrue(handled)
            self.assertIn("concubine_heart", state_module.state["action_guard_sessions"])
            self.assertEqual("heart_choice_pending", state_module.state["concubine_phase"])
            self.assertEqual(20, state_module.state["concubine_heart_prompt_msg_id"])
            self.assertEqual(1, state_module.state["concubine_heart_round"])

    async def test_status_snapshot_does_not_clear_active_heart_prompt(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        panel_text = (
            "你的红尘道侣: 【若兰】 (状态: 随行中)\n\n"
            "她安静地陪伴着你，虽不通星宫秘法，却也可为你牵引第二期机缘。\n\n"
            "【第二期机缘】\n"
            "- 入梦寻图冷却: 430分钟\n"
            "- 共历心劫冷却: 可施展\n"
            "- 天机代卜冷却: 199分钟\n"
            "- 梦图拼片: 虚天 3/4 | 苍坤 1/4\n"
            "命令: .入梦寻图、.残图、.拼图、.共历心劫、.天机代卜"
        )

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "heart_choice_pending"
            identity_state["concubine_heart_msg_id"] = 9296119
            identity_state["concubine_heart_prompt_msg_id"] = 9296120
            identity_state["concubine_heart_round"] = 1
            identity_state["concubine_heart_due_at"] = now + 3600
            identity_state["next_concubine_time"] = now + 20

        with state_module.use_identity(send_as_id):
            parsed = concubine._parse_status_panel(panel_text, now)
            self.assertTrue(parsed)
            self.assertTrue(concubine._apply_status_snapshot(parsed, now + 5))
            self.assertEqual((3, 4), concubine._get_fragment_progress(concubine.DREAM_KIND_XUTIAN))
            self.assertEqual((1, 4), concubine._get_fragment_progress(concubine.DREAM_KIND_CANGKUN))
            self.assertEqual(3, state_module.state["concubine_fragment_count"])
            self.assertEqual(4, state_module.state["concubine_fragment_total"])
            self.assertEqual("heart_choice_pending", state_module.state["concubine_phase"])
            self.assertEqual(9296119, state_module.state["concubine_heart_msg_id"])
            self.assertEqual(9296120, state_module.state["concubine_heart_prompt_msg_id"])
            self.assertEqual(1, state_module.state["concubine_heart_round"])
            self.assertEqual(now + 20, state_module.state["next_concubine_time"])
            self.assertEqual(now + 3600, state_module.state["concubine_heart_due_at"])

    async def test_status_snapshot_does_not_regress_future_tianji_cooldown(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        panel_text = (
            "你的道心侍妾: 【凌玉灵】 (状态: 随行中)\n\n"
            "情缘值: 1000\n"
            "【掩月心契】\n"
            "- 当前誓约: 守秘\n"
            "【第二期机缘】\n"
            "- 天机代卜链: 无\n"
            "- 入梦寻图冷却: 可施展\n"
            "- 共历心劫冷却: 可施展\n"
            "- 天机代卜冷却: 可施展\n"
            "- 梦图拼片: 虚天 0/4 | 苍坤 0/4\n"
            "命令: .入梦寻图、.残图、.拼图、.共历心劫、.天机代卜"
        )

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_due_at"] = now + 11 * 3600
            identity_state["concubine_tianji_chain"] = "残图引路"
            identity_state["concubine_tianji_chain_due_at"] = now + 11 * 3600

        with state_module.use_identity(send_as_id):
            parsed = concubine._parse_status_panel(panel_text, now + 60)
            self.assertTrue(parsed)
            self.assertTrue(concubine._apply_status_snapshot(parsed, now + 60))
            self.assertEqual(now + 11 * 3600, state_module.state["concubine_tianji_due_at"])
            self.assertEqual(0, state_module.state["concubine_tianji_chain_due_at"])

    async def test_cangkun_dream_broadcast_does_not_overwrite_xutian_progress(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_DREAM, id=501)

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 501
            concubine._set_fragment_progress(concubine.DREAM_KIND_XUTIAN, 2, 4)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()):
            handled = await concubine.handle_concubine_dream_reply(
                "【全群异闻·苍坤残图】\n道友共梦归来，残图进度已至 4/4。",
                now,
                reply_to,
                matched_family="concubine_dream",
            )

        self.assertTrue(handled)
        self.assertEqual((2, 4), concubine._get_fragment_progress(concubine.DREAM_KIND_XUTIAN))
        self.assertEqual((4, 4), concubine._get_fragment_progress(concubine.DREAM_KIND_CANGKUN))
        self.assertEqual(2, state_module.state["concubine_fragment_count"])

    async def test_pending_dream_without_reply_id_does_not_clear_current_pending(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 501
            identity_state["next_concubine_time"] = now + 60

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state") as save_mock, \
             patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock:
            handled = await concubine.handle_concubine_dream_reply(
                "这是另一条不带回复关系的入梦相关消息。",
                now,
                reply_to=None,
                matched_family="concubine_dream",
            )

        self.assertTrue(handled)
        save_mock.assert_not_called()
        self.assertEqual("dream_pending", state_module.state["concubine_phase"])
        self.assertEqual(501, state_module.state["concubine_dream_msg_id"])
        self.assertEqual(now + 60, state_module.state["next_concubine_time"])
        summaries = self._inbox_summaries(inbox_mock)
        self.assertTrue(any("忽略入梦寻图回复" in summary and "expected_msg_id=501" in summary for summary in summaries))

    async def test_status_timeout_recovers_reply_from_message_log(self):
        now = 1_700_000_900.0
        send_as_id = self._prepare_identity()
        panel_text = (
            "你的道心侍妾: 【凌玉灵】 (状态: 随行中)\n\n"
            "情缘值: 1000\n"
            "已解锁神通:\n - 【天机卜算】: 可在你观星冷却时代卜一次。\n\n"
            "【第二期机缘】\n"
            "- 天机代卜链: 无\n"
            "- 入梦寻图冷却: 120分钟\n"
            "- 共历心劫冷却: 30分钟\n"
            "- 天机代卜冷却: 60分钟\n"
            "- 梦图拼片: 虚天 1/4 | 苍坤 2/4\n"
            "命令: .入梦寻图、.残图、.拼图、.共历心劫、.天机代卜"
        )

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "status_pending"
            identity_state["concubine_status_msg_id"] = 501
            identity_state["next_concubine_time"] = now - 1
            identity_state["concubine_last_error"] = "pending"

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(
                tmpdir,
                [
                    {
                        "ts": self._log_ts(now - 10),
                        "event_type": "message",
                        "message_id": 602,
                        "reply_to_msg_id": 501,
                        "text": panel_text,
                    }
                ],
                now,
            )
            with state_module.use_identity(send_as_id), \
                 patch.object(concubine, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_audit_log", new=AsyncMock()), \
                 patch.object(concubine.random, "uniform", return_value=30), \
                 patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
                await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_called()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_status_msg_id"])
        self.assertEqual(602, state_module.state["concubine_last_panel_msg_id"])
        self.assertEqual("凌玉灵", state_module.state["concubine_name"])
        self.assertEqual("", state_module.state["concubine_last_error"])

    async def test_dream_timeout_recovers_reply_from_message_log(self):
        now = 1_700_000_900.0
        send_as_id = self._prepare_identity()
        reply_text = (
            "【入梦寻图】\n"
            "本次梦兆锁定：【虚天残图】 线路。\n"
            "你与侍妾【凌玉灵】共入迷梦，觅得【虚天残图】碎片。\n"
            "本次掉落率：28%（当前 虚天残图 2/4）。"
        )

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 701
            identity_state["next_concubine_time"] = now - 1
            identity_state["concubine_last_error"] = "pending"

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(
                tmpdir,
                [
                    {
                        "ts": self._log_ts(now - 10),
                        "event_type": "message",
                        "message_id": 702,
                        "reply_to_msg_id": 701,
                        "text": reply_text,
                    }
                ],
                now,
            )
            with state_module.use_identity(send_as_id), \
                 patch.object(concubine, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_audit_log", new=AsyncMock()), \
                 patch.object(concubine.random, "uniform", return_value=0), \
                 patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
                await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_called()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_dream_msg_id"])
        self.assertGreater(state_module.state["concubine_dream_due_at"], now)
        self.assertEqual("", state_module.state["concubine_last_error"])

    async def test_dream_timeout_ignores_message_log_reply_from_other_topic(self):
        now = 1_700_000_900.0
        send_as_id = self._prepare_identity()
        reply_text = (
            "【入梦寻图】\n"
            "本次梦兆锁定：【虚天残图】 线路。\n"
            "你与侍妾【凌玉灵】共入迷梦，觅得【虚天残图】碎片。\n"
            "本次掉落率：28%（当前 虚天残图 2/4）。"
        )

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 701
            identity_state["next_concubine_time"] = now - 1
            identity_state["concubine_last_error"] = "pending"

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(
                tmpdir,
                [
                    {
                        "ts": self._log_ts(now - 10),
                        "event_type": "message",
                        "message_id": 702,
                        "topic_id": 458347,
                        "reply_to_msg_id": 701,
                        "text": reply_text,
                    }
                ],
                now,
            )
            with state_module.use_identity(send_as_id), \
                 patch.object(concubine, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine, "get_game_topic_id", return_value=7310786), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_audit_log", new=AsyncMock()), \
                 patch.object(concubine.random, "uniform", return_value=0), \
                 patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
                await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_called()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_dream_msg_id"])
        self.assertEqual("dream_pending 等待回复超时，已转状态校准", state_module.state["concubine_last_error"])

    async def test_heart_choice_timeout_recovers_edit_from_message_log(self):
        now = 1_700_000_900.0
        send_as_id = self._prepare_identity()
        edit_text = (
            "【坠魔心劫·第1轮已定】\n"
            "你稳守灵台，不贪快功，魔影首轮试探未能动你分毫。\n\n"
            "【坠魔心劫·第2轮】\n"
            "幻境再变，请继续回复 .稳 / .狠 / .骗。"
        )

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "heart_choice_reply_pending"
            identity_state["concubine_heart_msg_id"] = 800
            identity_state["concubine_heart_prompt_msg_id"] = 901
            identity_state["concubine_heart_round"] = 1
            identity_state["concubine_heart_choice_prompt_msg_id"] = 901
            identity_state["concubine_heart_choice_round"] = 1
            identity_state["concubine_heart_choice_sent_at"] = now - 20
            identity_state["next_concubine_time"] = now - 1

        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_message_log(
                tmpdir,
                [
                    {
                        "ts": self._log_ts(now - 10),
                        "event_type": "edit",
                        "message_id": 901,
                        "reply_to_msg_id": 800,
                        "text": edit_text,
                    }
                ],
                now,
            )
            with state_module.use_identity(send_as_id), \
                 patch.object(concubine, "MESSAGES_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_audit_log", new=AsyncMock()), \
                 patch.object(concubine.random, "uniform", return_value=3), \
                 patch.object(concubine, "_schedule_heart_choice_followup", return_value=True), \
                 patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
                await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_called()
        self.assertEqual("heart_choice_pending", state_module.state["concubine_phase"])
        self.assertEqual(2, state_module.state["concubine_heart_round"])
        self.assertEqual(901, state_module.state["concubine_heart_prompt_msg_id"])
        self.assertEqual(now - 7, state_module.state["next_concubine_time"])

    async def test_cangkun_puzzle_success_clears_only_cangkun_progress(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(dream_due_at=now + 3600)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_PUZZLE, id=777)

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "puzzle_pending"
            identity_state["concubine_puzzle_msg_id"] = 777
            concubine._set_fragment_progress(concubine.DREAM_KIND_XUTIAN, 3, 4)
            concubine._set_fragment_progress(concubine.DREAM_KIND_CANGKUN, 4, 4)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()):
            handled = await concubine.handle_concubine_puzzle_reply(
                "【苍坤残图·拼合成功】\n苍坤洞府舆图已成，修为 +120。",
                now,
                reply_to,
                matched_family="concubine_puzzle",
            )

        self.assertTrue(handled)
        self.assertEqual((3, 4), concubine._get_fragment_progress(concubine.DREAM_KIND_XUTIAN))
        self.assertEqual((0, 4), concubine._get_fragment_progress(concubine.DREAM_KIND_CANGKUN))
        self.assertEqual(3, state_module.state["concubine_fragment_count"])

    async def test_fragment_confirmation_promotes_puzzle_and_blocks_repeat_fragment_send(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(dream_due_at=now + 3600, tianji_due_at=now + 3600)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_FRAGMENT, id=701)

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "fragment_pending"
            identity_state["concubine_fragment_msg_id"] = 701
            identity_state["next_concubine_time"] = now
            concubine._set_fragment_progress(concubine.DREAM_KIND_XUTIAN, 3, 4)
            concubine._set_fragment_progress(concubine.DREAM_KIND_CANGKUN, 4, 4)

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_fragment_reply(
                "侍妾【月婵】（随行中）的残图卷轴如下：\n\n"
                "【虚天残图卷】\n"
                "拼片进度：3/4\n"
                "已收集：北阙残纹、南渊残纹、西极残纹\n"
                "缺失残纹：东离残纹\n"
                "重复藏本：北阙残纹x4、南渊残纹x3\n\n"
                "【苍坤残图卷】\n"
                "拼片进度：4/4\n"
                "已收集：慕兰残纹、禁门残纹、玉匣残纹、太妙残纹\n"
                "缺失残纹：无\n"
                "重复藏本：禁门残纹x2",
                now,
                reply_to,
                matched_family="concubine_fragment",
            )

        self.assertTrue(handled)
        self.assertEqual("puzzle_ready", state_module.state["concubine_phase"])
        self.assertEqual("cangkun:4/4", state_module.state["concubine_fragment_confirm_key"])
        self.assertEqual(now, state_module.state["concubine_fragment_confirmed_at"])
        self.assertEqual(now, state_module.state["next_concubine_time"])

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "idle"
            identity_state["concubine_puzzle_msg_id"] = 0
            identity_state["next_concubine_time"] = now

        sent_msg = SimpleNamespace(id=888, sent_at=now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_awaited_once_with(config.CMD_CONCUBINE_PUZZLE, track=False, priority="chain")
        self.assertEqual("puzzle_pending", state_module.state["concubine_phase"])
        self.assertEqual(888, state_module.state["concubine_puzzle_msg_id"])
        self.assertEqual("cangkun:4/4", state_module.state["concubine_fragment_confirm_key"])

    async def test_orphan_heart_prompt_blocks_new_heart_command(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "idle"
            identity_state["concubine_last_panel_msg_id"] = 9296114
            identity_state["concubine_last_snapshot_at"] = now - 5
            identity_state["concubine_heart_due_at"] = now - 1
            identity_state["concubine_heart_prompt_msg_id"] = 9296120
            identity_state["concubine_heart_round"] = 1
            identity_state["next_concubine_time"] = 0

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=12), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            sent = await concubine._send_heart_command(now)
            self.assertFalse(sent)
            mock_send.assert_not_awaited()
            self.assertEqual("heart_choice_pending", state_module.state["concubine_phase"])
            self.assertEqual(now + 12, state_module.state["next_concubine_time"])

    async def test_heart_edit_prompt_without_reply_context_advances_next_round(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        prompt_msg_id = 9384547
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "heart_choice_reply_pending"
            identity_state["concubine_heart_prompt_msg_id"] = prompt_msg_id
            identity_state["concubine_heart_round"] = 1
            identity_state["concubine_heart_choice_prompt_msg_id"] = prompt_msg_id
            identity_state["concubine_heart_choice_round"] = 1
            identity_state["concubine_heart_choice_sent_at"] = now - 10

        text = (
            "【坠魔心劫·第1轮已定】\n"
            "你稳守灵台，不贪快功，魔影首轮试探未能动你分毫。\n\n"
            "【坠魔心劫·第2轮】\n"
            "幻境再变，请继续回复 .稳 / .狠 / .骗。"
        )
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=12):
            handled = await concubine.handle_concubine_heart_reply(
                text,
                now,
                reply_to=None,
                matched_family=None,
                current_msg_id=prompt_msg_id,
            )

        self.assertTrue(handled)
        self.assertEqual("heart_choice_pending", state_module.state["concubine_phase"])
        self.assertEqual(prompt_msg_id, state_module.state["concubine_heart_prompt_msg_id"])
        self.assertEqual(2, state_module.state["concubine_heart_round"])
        self.assertEqual("", state_module.state["concubine_heart_last_error"])
        self.assertEqual(now + 12, state_module.state["next_concubine_time"])

    async def test_heart_edit_can_jump_to_third_round_from_real_prompt_text(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        prompt_msg_id = 9387375
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "heart_choice_reply_pending"
            identity_state["concubine_heart_prompt_msg_id"] = prompt_msg_id
            identity_state["concubine_heart_round"] = 1
            identity_state["concubine_heart_choice_prompt_msg_id"] = prompt_msg_id
            identity_state["concubine_heart_choice_round"] = 1
            identity_state["concubine_heart_choice_sent_at"] = now - 10

        text = (
            "【坠魔心劫·第2轮已定】\n"
            "你按韩立式谨慎节奏步步为营，侍妾神念与你渐趋同频。\n\n"
            "【坠魔心劫·第3轮】\n"
            "幻境再变，请继续回复 .稳 / .狠 / .骗。"
        )
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=3):
            handled = await concubine.handle_concubine_heart_reply(
                text,
                now,
                reply_to=None,
                matched_family=None,
                current_msg_id=prompt_msg_id,
            )

        self.assertTrue(handled)
        self.assertEqual("heart_choice_pending", state_module.state["concubine_phase"])
        self.assertEqual(prompt_msg_id, state_module.state["concubine_heart_prompt_msg_id"])
        self.assertEqual(3, state_module.state["concubine_heart_round"])
        self.assertEqual(now + 3, state_module.state["next_concubine_time"])

    async def test_passive_inbox_recovers_first_heart_prompt_without_send_as_id(self):
        now = 1_780_413_865.0
        send_as_id = self._prepare_identity()
        text = (
            "【坠魔心劫·第一轮】\n"
            "你与侍妾【绾绾】步入幻境，前方魔念化形拦路。\n"
            "请回复本消息 .稳 / .狠 / .骗 进行抉择（共3轮）。\n"
            "【天机前兆】已生效：本次心劫入场消耗降低，首轮评分+1。"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "heart_pending"
            identity_state["concubine_heart_msg_id"] = 9746304
            identity_state["concubine_heart_prompt_msg_id"] = 0

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state"), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=2), \
             patch.object(concubine, "_schedule_heart_choice_followup", return_value=True):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "family": "concubine_heart",
                    "reply_to_msg_id": 9746304,
                    "root_msg_id": 9746304,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=9746308),
                event_type="message",
            )

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            self.assertEqual("heart_choice_pending", state_module.state["concubine_phase"])
            self.assertEqual(9746308, state_module.state["concubine_heart_prompt_msg_id"])
            self.assertEqual(1, state_module.state["concubine_heart_round"])
            self.assertEqual(now + 2, state_module.state["next_concubine_time"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["changed"])
        self.assertIn("concubine", snapshot["modules"])

    async def test_passive_inbox_recovers_heart_edit_and_settlement_without_send_as_id(self):
        now = 1_780_413_875.0
        send_as_id = self._prepare_identity()
        prompt_msg_id = 9746308
        edit_text = (
            "【坠魔心劫·第1轮已定】\n"
            "你稳守灵台，不贪快功，魔影首轮试探未能动你分毫。\n\n"
            "【坠魔心劫·第2轮】\n"
            "幻境再变，请继续回复 .稳 / .狠 / .骗。"
        )
        settlement_text = (
            "【坠魔心劫·结算】\n"
            "三轮抉择：稳 / 稳 / 稳\n"
            "你以守代攻，借势封魔，终在险境中稳稳落子。\n"
            "你以韩立式谨慎贯穿全局，收益最大。\n\n"
            "修为结算：+823\n"
            "情缘结算：+7\n"
            "心魔值结算：-5（当前 0）"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "heart_choice_reply_pending"
            identity_state["concubine_heart_msg_id"] = 9746304
            identity_state["concubine_heart_prompt_msg_id"] = prompt_msg_id
            identity_state["concubine_heart_round"] = 1
            identity_state["concubine_heart_choice_prompt_msg_id"] = prompt_msg_id
            identity_state["concubine_heart_choice_round"] = 1
            identity_state["concubine_heart_choice_sent_at"] = now - 10

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state"), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=2), \
             patch.object(concubine, "_schedule_heart_choice_followup", return_value=True), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()):
            handled_edit = await passive_inbox.handle_passive_module_card(
                edit_text,
                now=now,
                reply_context={
                    "family": "concubine_heart",
                    "reply_to_msg_id": 9746304,
                    "root_msg_id": 9746304,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=prompt_msg_id),
                event_type="edit",
            )
            handled_settlement = await passive_inbox.handle_passive_module_card(
                settlement_text,
                now=now + 20,
                reply_context={
                    "family": "concubine_heart",
                    "reply_to_msg_id": 9746304,
                    "root_msg_id": 9746304,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=prompt_msg_id),
                event_type="edit",
            )

        self.assertTrue(handled_edit)
        self.assertTrue(handled_settlement)
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(0, state_module.state["concubine_heart_prompt_msg_id"])
            self.assertEqual(0, state_module.state["concubine_heart_round"])
            self.assertGreater(state_module.state["concubine_heart_due_at"], now)

    async def test_passive_inbox_recovers_dream_reply_from_pending_chain_without_send_as_id(self):
        now = 1_780_414_158.0
        send_as_id = self._prepare_identity()
        text = (
            "【入梦寻图】\n"
            "本次梦兆锁定：【苍坤残图】 线路。\n"
            "你与侍妾【洛神】共入迷梦，终只见荒沙蔽月，未觅得【苍坤残图】碎片。\n"
            "另一条残图线路仍可能在后续 .入梦寻图 中显化。\n"
            "本次掉落率：22%（进度衰减 -12%，当前 苍坤残图 2/4）。"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = True
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 9746562
            identity_state["concubine_fragment_cangkun_count"] = 1
            identity_state["concubine_fragment_cangkun_total"] = 4

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state"), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "family": "concubine_dream",
                    "reply_to_msg_id": 9746562,
                    "root_msg_id": 9746562,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=9746564),
                event_type="message",
            )

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(0, state_module.state["concubine_dream_msg_id"])
            self.assertEqual((2, 4), concubine._get_fragment_progress(concubine.DREAM_KIND_CANGKUN))
            self.assertGreater(state_module.state["concubine_dream_due_at"], now)

    async def test_passive_inbox_recovers_dream_cooldown_from_pending_chain_without_send_as_id(self):
        now = 1_780_471_316.0
        send_as_id = self._prepare_identity()
        text = "梦图感应尚未重启，请在 7小时3分钟22秒 后再试。"
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = True
            identity_state["concubine_phase"] = "dream_pending"
            identity_state["concubine_dream_msg_id"] = 9779196
            identity_state["concubine_last_error"] = "pending"

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state"), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "family": "concubine_dream",
                    "reply_to_msg_id": 9779196,
                    "root_msg_id": 9779196,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=9779198),
                event_type="message",
            )

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(0, state_module.state["concubine_dream_msg_id"])
            self.assertEqual("", state_module.state["concubine_last_error"])
            self.assertEqual(now + 25402 + config.CD_BUFFER_SEC, state_module.state["concubine_dream_due_at"])

    async def test_passive_inbox_recovers_tianji_reply_from_pending_chain_without_send_as_id(self):
        now = 1_780_417_211.0
        send_as_id = self._prepare_identity()
        text = (
            "【天机代卜链】\n"
            "侍妾【红莲】焚香推演，为你接引一缕天机。\n"
            "得卦【心劫前兆】：下一次 .共历心劫 成本降低且首轮评分提高。\n"
            "本次消耗：180修为。"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_enabled"] = True
            identity_state["concubine_phase"] = "tianji_pending"
            identity_state["concubine_tianji_msg_id"] = 9747210

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state"), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "family": "concubine_tianji",
                    "reply_to_msg_id": 9747210,
                    "root_msg_id": 9747210,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=9747211),
                event_type="message",
            )

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(0, state_module.state["concubine_tianji_msg_id"])
            self.assertEqual("心劫前兆", state_module.state["concubine_tianji_chain"])
            self.assertGreater(state_module.state["concubine_tianji_due_at"], now)

    async def test_passive_inbox_recovers_tianji_cooldown_from_pending_chain_without_send_as_id(self):
        now = 1_780_471_072.0
        send_as_id = self._prepare_identity()
        text = "天机链路尚未重铸，请在 11小时9分钟29秒 后再试。"
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_tianji_enabled"] = True
            identity_state["concubine_phase"] = "tianji_pending"
            identity_state["concubine_tianji_msg_id"] = 9778978
            identity_state["concubine_tianji_last_error"] = "pending"

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state"), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "family": "concubine_tianji",
                    "reply_to_msg_id": 9778978,
                    "root_msg_id": 9778978,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=9778979),
                event_type="message",
            )

        self.assertTrue(handled)
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["concubine_phase"])
            self.assertEqual(0, state_module.state["concubine_tianji_msg_id"])
            self.assertEqual("", state_module.state["concubine_tianji_last_error"])
            self.assertEqual(now + 40169 + config.CD_BUFFER_SEC, state_module.state["concubine_tianji_due_at"])

    async def test_passive_inbox_does_not_recover_ambiguous_heart_context(self):
        now = 1_780_413_875.0
        first_id = self._prepare_identity()
        second_id = 991102
        state_module.ensure_identity_registered(second_id)
        state_module.update_send_as_profile(second_id, username="second")
        text = (
            "【坠魔心劫·第1轮已定】\n"
            "你稳守灵台，不贪快功，魔影首轮试探未能动你分毫。\n\n"
            "【坠魔心劫·第2轮】\n"
            "幻境再变，请继续回复 .稳 / .狠 / .骗。"
        )
        for identity_id in (first_id, second_id):
            with state_module.use_identity(identity_id) as identity_state:
                identity_state["concubine_heart_enabled"] = True
                identity_state["concubine_phase"] = "heart_choice_reply_pending"
                identity_state["concubine_heart_msg_id"] = 9746304
                identity_state["concubine_heart_prompt_msg_id"] = 9746308
                identity_state["concubine_heart_round"] = 1

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state"), \
             patch.object(concubine, "save_state"):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "family": "concubine_heart",
                    "reply_to_msg_id": 9746304,
                    "root_msg_id": 9746304,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=9746308),
                event_type="edit",
            )

        self.assertFalse(handled)
        with state_module.use_identity(first_id):
            self.assertEqual(1, state_module.state["concubine_heart_round"])
        with state_module.use_identity(second_id):
            self.assertEqual(1, state_module.state["concubine_heart_round"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["skipped"])
        self.assertEqual(1, snapshot["skip_reasons"].get("reply_context_no_identity"))

    async def test_passive_inbox_does_not_recover_ambiguous_dream_pending_context(self):
        now = 1_780_414_158.0
        first_id = self._prepare_identity()
        second_id = 991102
        state_module.ensure_identity_registered(second_id)
        state_module.update_send_as_profile(second_id, username="second")
        text = (
            "【入梦寻图】\n"
            "本次梦兆锁定：【苍坤残图】 线路。\n"
            "你与侍妾【洛神】共梦慕兰荒原，获得 【苍坤残图】 残纹 慕兰残纹（新残纹）。\n"
            "当前进度：3/4。"
        )
        for identity_id in (first_id, second_id):
            with state_module.use_identity(identity_id) as identity_state:
                identity_state["concubine_enabled"] = True
                identity_state["concubine_phase"] = "dream_pending"
                identity_state["concubine_dream_msg_id"] = 9746562

        with patch.object(passive_inbox, "_save_passive_stats"), \
             patch.object(passive_inbox, "save_state"), \
             patch.object(concubine, "save_state"):
            handled = await passive_inbox.handle_passive_module_card(
                text,
                now=now,
                reply_context={
                    "family": "concubine_dream",
                    "reply_to_msg_id": 9746562,
                    "root_msg_id": 9746562,
                },
                event=SimpleNamespace(chat_id=-1001680975844, id=9746564),
                event_type="message",
            )

        self.assertFalse(handled)
        with state_module.use_identity(first_id):
            self.assertEqual("dream_pending", state_module.state["concubine_phase"])
        with state_module.use_identity(second_id):
            self.assertEqual("dream_pending", state_module.state["concubine_phase"])
        snapshot = passive_inbox.get_passive_inbox_snapshot()
        self.assertEqual(1, snapshot["skipped"])
        self.assertEqual(1, snapshot["skip_reasons"].get("reply_context_no_identity"))

    def test_heart_choice_delay_is_fast_enough_for_edited_prompt(self):
        with patch.object(concubine.random, "uniform", return_value=2) as mock_uniform:
            self.assertEqual(2, concubine._heart_next_choice_delay())
        mock_uniform.assert_called_once_with(1, 3)

    def test_heart_choice_round_schedules_fast_followup(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        prompt_msg_id = 9387665

        with state_module.use_identity(send_as_id), \
             patch.object(concubine.random, "uniform", return_value=2), \
             patch.object(concubine, "_schedule_heart_choice_followup", return_value=True) as mock_followup:
            concubine._activate_heart_choice_round(now, prompt_msg_id, 2)

        self.assertEqual("heart_choice_pending", state_module.state["concubine_phase"])
        self.assertEqual(prompt_msg_id, state_module.state["concubine_heart_prompt_msg_id"])
        self.assertEqual(2, state_module.state["concubine_heart_round"])
        self.assertEqual(now + 2, state_module.state["next_concubine_time"])
        mock_followup.assert_called_once_with(send_as_id, now + 2, prompt_msg_id, 2)

    def test_heart_choice_round_replay_does_not_regress_same_prompt(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        prompt_msg_id = 9387665

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "heart_choice_pending"
            identity_state["concubine_heart_prompt_msg_id"] = prompt_msg_id
            identity_state["concubine_heart_round"] = 3
            identity_state["next_concubine_time"] = now + 2

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "_schedule_heart_choice_followup", return_value=True) as mock_followup:
            concubine._activate_heart_choice_round(now + 1, prompt_msg_id, 2)

        self.assertEqual("heart_choice_pending", state_module.state["concubine_phase"])
        self.assertEqual(prompt_msg_id, state_module.state["concubine_heart_prompt_msg_id"])
        self.assertEqual(3, state_module.state["concubine_heart_round"])
        self.assertEqual(now + 2, state_module.state["next_concubine_time"])
        mock_followup.assert_not_called()

    def test_heart_choice_round_replay_does_not_reschedule_same_pending_round(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        prompt_msg_id = 9387665

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "heart_choice_pending"
            identity_state["concubine_heart_prompt_msg_id"] = prompt_msg_id
            identity_state["concubine_heart_round"] = 2
            identity_state["next_concubine_time"] = now + 2

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "_schedule_heart_choice_followup", return_value=True) as mock_followup:
            concubine._activate_heart_choice_round(now + 1, prompt_msg_id, 2)

        self.assertEqual("heart_choice_pending", state_module.state["concubine_phase"])
        self.assertEqual(2, state_module.state["concubine_heart_round"])
        self.assertEqual(now + 2, state_module.state["next_concubine_time"])
        mock_followup.assert_not_called()

    async def test_heart_choice_send_uses_urgent_reactive_queue(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        prompt_msg_id = 9387665
        sent_msg = SimpleNamespace(id=9387668, sent_at=now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "heart_choice_pending"
            identity_state["concubine_heart_prompt_msg_id"] = prompt_msg_id
            identity_state["concubine_heart_round"] = 2

        with tempfile.TemporaryDirectory() as tmpdir:
            with state_module.use_identity(send_as_id), \
                 patch.object(workflow_log, "WORKFLOW_LOG_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
                sent = await concubine._send_heart_choice(now)
                workflow_events = _read_workflow_events(tmpdir)

        self.assertTrue(sent)
        mock_send.assert_awaited_once_with(
            config.CMD_CONCUBINE_HEART_STEADY,
            track=False,
            reply_to=prompt_msg_id,
            priority="urgent_reactive",
        )
        self.assertEqual("heart_choice_reply_pending", state_module.state["concubine_phase"])
        self.assertEqual(prompt_msg_id, state_module.state["concubine_heart_choice_prompt_msg_id"])
        self.assertEqual(2, state_module.state["concubine_heart_choice_round"])
        self.assertTrue(any(
            event.get("workflow") == "concubine"
            and event.get("decision") == "heart_choice_sent"
            and event.get("family") == "concubine_heart"
            and event.get("command") == config.CMD_CONCUBINE_HEART_STEADY
            and event.get("msg_id") == sent_msg.id
            and "prompt_msg_id=9387665" in event.get("detail", {}).get("detail", "")
            for event in workflow_events
        ))

    async def test_heart_choice_guard_blocks_duplicate_same_round_send(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        prompt_msg_id = 9384918
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "heart_choice_pending"
            identity_state["concubine_heart_prompt_msg_id"] = prompt_msg_id
            identity_state["concubine_heart_round"] = 2
            identity_state["concubine_heart_choice_prompt_msg_id"] = prompt_msg_id
            identity_state["concubine_heart_choice_round"] = 2
            identity_state["concubine_heart_choice_sent_at"] = now - 10

        with tempfile.TemporaryDirectory() as tmpdir:
            with state_module.use_identity(send_as_id), \
                 patch.object(workflow_log, "WORKFLOW_LOG_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
                sent = await concubine._send_heart_choice(now)
                workflow_events = _read_workflow_events(tmpdir)

        self.assertFalse(sent)
        mock_send.assert_not_awaited()
        self.assertEqual("heart_choice_reply_pending", state_module.state["concubine_phase"])
        self.assertEqual(now + 35, state_module.state["next_concubine_time"])
        self.assertIn("已发送 .稳", state_module.state["concubine_heart_last_error"])
        self.assertTrue(any(
            event.get("workflow") == "concubine"
            and event.get("decision") == "heart_choice_duplicate_guard"
            and event.get("status") == "skipped"
            and event.get("family") == "concubine_heart"
            and event.get("command") == config.CMD_CONCUBINE_HEART_STEADY
            and "round=2" in event.get("detail", {}).get("detail", "")
            for event in workflow_events
        ))

    async def test_heart_edit_after_sent_choice_advances_round_without_duplicate(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        prompt_msg_id = 9384918
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "heart_choice_reply_pending"
            identity_state["concubine_heart_prompt_msg_id"] = prompt_msg_id
            identity_state["concubine_heart_round"] = 1
            identity_state["concubine_heart_choice_prompt_msg_id"] = prompt_msg_id
            identity_state["concubine_heart_choice_round"] = 1
            identity_state["concubine_heart_choice_sent_at"] = now - 10

        text = (
            "【坠魔心劫·第1轮已定】\n"
            "你稳守灵台，不贪快功，魔影首轮试探未能动你分毫。\n\n"
            "【坠魔心劫·第2轮】\n"
            "幻境再变，请继续回复 .稳 / .狠 / .骗。"
        )
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=9), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            handled = await concubine.handle_concubine_heart_reply(
                text,
                now,
                reply_to=SimpleNamespace(raw_text=config.CMD_CONCUBINE_HEART, id=prompt_msg_id),
                matched_family="concubine_heart",
                current_msg_id=prompt_msg_id,
            )

        self.assertTrue(handled)
        mock_send.assert_not_awaited()
        self.assertEqual("heart_choice_pending", state_module.state["concubine_phase"])
        self.assertEqual(prompt_msg_id, state_module.state["concubine_heart_prompt_msg_id"])
        self.assertEqual(2, state_module.state["concubine_heart_round"])
        self.assertEqual(1, state_module.state["concubine_heart_choice_round"])
        self.assertEqual(now + 9, state_module.state["next_concubine_time"])

    async def test_heart_choice_timeout_closes_guard_and_uses_long_cooldown(self):
        now = 1_700_000_900.0
        sent_at = now - 60
        send_as_id = self._prepare_identity()
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "heart_choice_reply_pending"
            identity_state["concubine_heart_msg_id"] = 9387528
            identity_state["concubine_heart_prompt_msg_id"] = 9387530
            identity_state["concubine_heart_round"] = 3
            identity_state["concubine_heart_choice_prompt_msg_id"] = 9387530
            identity_state["concubine_heart_choice_round"] = 3
            identity_state["concubine_heart_choice_sent_at"] = sent_at
            identity_state["next_concubine_time"] = now - 1
            identity_state["action_guard_sessions"] = {
                "concubine_heart": {
                    "action_key": "concubine_heart",
                    "attempt": 1,
                    "last_sent_at": sent_at - 10,
                    "first_sent_at": sent_at - 10,
                    "next_allowed_at": now + 600,
                    "last_msg_id": 9387528,
                }
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            with state_module.use_identity(send_as_id), \
                 patch.object(workflow_log, "WORKFLOW_LOG_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_audit_log", new=AsyncMock()) as mock_audit, \
                 patch.object(concubine, "_recover_concubine_pending_from_message_log", new=AsyncMock(return_value=False)), \
                 patch.object(concubine.random, "uniform", return_value=60):
                await concubine.run_concubine_scheduler(now)
                workflow_events = _read_workflow_events(tmpdir)

        expected_due = sent_at + config.CONCUBINE_HEART_CD_SEC + config.CD_BUFFER_SEC
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_heart_prompt_msg_id"])
        self.assertEqual(0, state_module.state["concubine_heart_choice_sent_at"])
        self.assertNotIn("concubine_heart", state_module.state["action_guard_sessions"])
        self.assertEqual(expected_due, state_module.state["concubine_heart_due_at"])
        self.assertEqual(expected_due + 60, state_module.state["next_concubine_time"])
        self.assertNotEqual("发送 .共历心劫 失败", state_module.state["concubine_heart_last_error"])
        mock_audit.assert_awaited_once()
        self.assertTrue(any(
            event.get("workflow") == "concubine"
            and event.get("decision") == "heart_chain_closed_without_settlement"
            and event.get("detail", {}).get("reason") == "heart_choice_reply_timeout"
            for event in workflow_events
        ))

    async def test_heart_send_guard_block_is_not_recorded_as_send_failure(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        sent_at = now - 120
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_availability"] = "available"
            identity_state["concubine_name"] = "凌玉灵"
            identity_state["concubine_last_panel_msg_id"] = 9387319
            identity_state["concubine_last_snapshot_at"] = now
            identity_state["concubine_heart_due_at"] = now - 1
            identity_state["action_guard_sessions"] = {
                "concubine_heart": {
                    "action_key": "concubine_heart",
                    "attempt": 1,
                    "last_sent_at": sent_at,
                    "first_sent_at": sent_at,
                    "next_allowed_at": now + 600,
                    "last_msg_id": 9387528,
                }
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            with state_module.use_identity(send_as_id), \
                 patch.object(workflow_log, "WORKFLOW_LOG_DIR", tmpdir), \
                 patch.object(concubine, "save_state"), \
                 patch.object(concubine, "send_game_command", new=AsyncMock(return_value=None)) as mock_send, \
                 patch.object(concubine.random, "uniform", return_value=60):
                sent = await concubine._send_heart_command(now)
                workflow_events = _read_workflow_events(tmpdir)

        expected_due = sent_at + config.CONCUBINE_HEART_CD_SEC + config.CD_BUFFER_SEC
        self.assertFalse(sent)
        mock_send.assert_awaited_once_with(
            config.CMD_CONCUBINE_HEART,
            track=False,
            reply_to=9387319,
            priority="chain",
        )
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertNotIn("concubine_heart", state_module.state["action_guard_sessions"])
        self.assertEqual(expected_due, state_module.state["concubine_heart_due_at"])
        self.assertNotEqual("发送 .共历心劫 失败", state_module.state["concubine_heart_last_error"])
        self.assertTrue(any(
            event.get("workflow") == "concubine"
            and event.get("decision") == "heart_chain_closed_without_settlement"
            and event.get("detail", {}).get("reason") == "heart_send_blocked_by_stale_guard"
            for event in workflow_events
        ))

    def test_restore_concubine_runtime_reconciles_idle_heart_guard(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity()
        sent_at = now - 300
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_heart_enabled"] = True
            identity_state["concubine_phase"] = "idle"
            identity_state["concubine_heart_due_at"] = 0
            identity_state["concubine_heart_last_error"] = "发送 .共历心劫 失败"
            identity_state["action_guard_sessions"] = {
                "concubine_heart": {
                    "action_key": "concubine_heart",
                    "attempt": 1,
                    "last_sent_at": sent_at,
                    "first_sent_at": sent_at,
                    "next_allowed_at": now + 300,
                    "last_msg_id": 9387528,
                }
            }

        with tempfile.TemporaryDirectory() as tmpdir:
            with state_module.use_identity(send_as_id), \
                 patch.object(workflow_log, "WORKFLOW_LOG_DIR", tmpdir), \
                 patch.object(concubine.random, "uniform", return_value=60):
                next_time = concubine.restore_concubine_runtime(now)
                workflow_events = _read_workflow_events(tmpdir)

        expected_due = sent_at + config.CONCUBINE_HEART_CD_SEC + config.CD_BUFFER_SEC
        self.assertEqual(expected_due + 60, next_time)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertNotIn("concubine_heart", state_module.state["action_guard_sessions"])
        self.assertEqual(expected_due, state_module.state["concubine_heart_due_at"])
        self.assertNotEqual("发送 .共历心劫 失败", state_module.state["concubine_heart_last_error"])
        self.assertTrue(any(
            event.get("workflow") == "concubine"
            and event.get("decision") == "heart_chain_closed_without_settlement"
            and event.get("detail", {}).get("reason") == "heart_stale_guard_startup_restore"
            for event in workflow_events
        ))

    async def test_voyage_panel_blocks_due_concubine_actions(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now - 1, tianji_due_at=now - 1)
        panel_text = (
            "你的道心侍妾: 【柳玉】 (状态: 随行中)\n"
            "情缘值: 320\n"
            "当前誓约: 无\n"
            "远航状态: 均衡航线进行中，剩余约 56 分钟。\n"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            parsed = concubine._parse_status_panel(panel_text, now)
            self.assertIsNotNone(parsed)
            self.assertTrue(concubine._apply_status_snapshot(parsed, now))
            await concubine.run_concubine_scheduler(now)

        expected_return_at = now + 56 * 60 + config.CD_BUFFER_SEC
        mock_send.assert_not_awaited()
        self.assertEqual("sailing", state_module.state["concubine_voyage_status"])
        self.assertEqual("均衡", state_module.state["concubine_voyage_route"])
        self.assertEqual(expected_return_at, state_module.state["concubine_voyage_return_at"])
        self.assertEqual(expected_return_at, state_module.state["next_concubine_time"])

    async def test_tianji_reply_voyage_lock_sets_sailing_and_clears_pending(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now - 1)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "tianji_pending"
            identity_state["concubine_tianji_msg_id"] = 812

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_tianji_reply(
                "侍妾正在远航途中，暂无法焚香代卜。",
                now,
                SimpleNamespace(raw_text=config.CMD_CONCUBINE_TIANJI, id=812),
                matched_family="concubine_tianji",
            )

        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_tianji_msg_id"])
        self.assertEqual("sailing", state_module.state["concubine_voyage_status"])
        self.assertEqual(0, state_module.state["concubine_voyage_return_at"])
        self.assertEqual(now + concubine.CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC, state_module.state["next_concubine_time"])
        self.assertIn("天机代卜被远航锁拦截", state_module.state["concubine_tianji_last_error"])

    async def test_scheduler_returns_voyage_before_other_concubine_actions(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now - 1, tianji_due_at=now - 1)
        sent_msg = SimpleNamespace(id=913, sent_at=now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_voyage_status"] = "returned"
            identity_state["concubine_voyage_route"] = "冒险"
            identity_state["concubine_voyage_return_at"] = now
            identity_state["next_concubine_time"] = 0

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_awaited_once_with(config.CMD_CONCUBINE_VOYAGE_RETURN, track=False, priority="chain")
        self.assertEqual("voyage_return_pending", state_module.state["concubine_phase"])
        self.assertEqual(913, state_module.state["concubine_voyage_msg_id"])

    async def test_scheduler_starts_default_adventure_voyage_after_other_actions_clear(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        sent_msg = SimpleNamespace(id=914, sent_at=now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_enabled"] = False
            identity_state["concubine_heart_enabled"] = False
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_voyage_route"] = "均衡"
            identity_state["next_concubine_time"] = now + 3600

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_awaited_once_with(f"{config.CMD_CONCUBINE_VOYAGE} {config.CONCUBINE_VOYAGE_DEFAULT_ROUTE}", track=False, priority="chain")
        self.assertEqual("voyage_pending", state_module.state["concubine_phase"])
        self.assertEqual(914, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual(config.CONCUBINE_VOYAGE_DEFAULT_ROUTE, state_module.state["concubine_voyage_route"])

    async def test_scheduler_status_calibrates_when_only_voyage_lacks_partner_snapshot(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        sent_msg = SimpleNamespace(id=916, sent_at=now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_enabled"] = False
            identity_state["concubine_heart_enabled"] = False
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_availability"] = "unknown"
            identity_state["concubine_name"] = ""
            identity_state["next_concubine_time"] = 0

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_awaited_once_with(config.CMD_CONCUBINE_STATUS, track=False)
        self.assertEqual("status_pending", state_module.state["concubine_phase"])
        self.assertEqual(916, state_module.state["concubine_status_msg_id"])
        self.assertEqual(0, state_module.state["concubine_dream_msg_id"])

    async def test_scheduler_skips_voyage_when_identity_or_affinity_is_ineligible(self):
        now = 1_700_000_000.0
        low_affinity_id = self._prepare_identity(affinity=299, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(low_affinity_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_enabled"] = False
            identity_state["concubine_voyage_enabled"] = True
            identity_state["next_concubine_time"] = 0

        with state_module.use_identity(low_affinity_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=60), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertIn("情缘不足", state_module.state["concubine_voyage_last_error"])

        other_sect_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600, sect_name="落云宗")
        with state_module.use_identity(other_sect_id) as identity_state:
            identity_state["concubine_enabled"] = False
            identity_state["concubine_tianji_enabled"] = False
            identity_state["concubine_voyage_enabled"] = True
            identity_state["next_concubine_time"] = 0

        with state_module.use_identity(other_sect_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=60), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_not_awaited()
        self.assertIn("仅星宫身份", state_module.state["concubine_voyage_last_error"])

    async def test_voyage_return_reply_sets_idle_and_schedules_next_chain(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_VOYAGE_RETURN, id=915)
        text = (
            "【乱星海远航·归】\n"
            "侍妾【柳玉】已自 冒险 航线归来，向你呈上收获：\n"
            "灵石 x 100"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "voyage_return_pending"
            identity_state["concubine_voyage_msg_id"] = 915

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await concubine.handle_concubine_voyage_reply(
                text,
                now,
                reply_to,
                matched_family="concubine_voyage",
            )

        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual("idle", state_module.state["concubine_voyage_status"])
        self.assertEqual("冒险", state_module.state["concubine_voyage_route"])
        self.assertIn("灵石 x 100", state_module.state["concubine_voyage_last_result"])
        self.assertEqual(now + 30, state_module.state["next_concubine_time"])

    async def test_voyage_return_reply_with_remaining_cd_updates_return_at(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        reply_to = SimpleNamespace(raw_text=config.CMD_CONCUBINE_VOYAGE_RETURN, id=917)
        text = "侍妾尚未归航，预计归航还需 56 分钟。"
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "voyage_return_pending"
            identity_state["concubine_voyage_status"] = "sailing"
            identity_state["concubine_voyage_msg_id"] = 917
            identity_state["concubine_voyage_retry_count"] = 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=0):
            handled = await concubine.handle_concubine_voyage_reply(
                text,
                now,
                reply_to,
                matched_family="concubine_voyage",
            )

        expected_return_at = now + 56 * 60 + config.CD_BUFFER_SEC
        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual(0, state_module.state["concubine_voyage_retry_count"])
        self.assertEqual("sailing", state_module.state["concubine_voyage_status"])
        self.assertEqual(expected_return_at, state_module.state["concubine_voyage_return_at"])
        self.assertEqual(expected_return_at, state_module.state["next_concubine_time"])

    async def test_voyage_return_no_task_clears_only_current_return_reply(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        text = "侍妾当前并无可结算的远航任务。"
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_voyage_status"] = "sailing"
            identity_state["concubine_voyage_route"] = "冒险"
            identity_state["concubine_voyage_return_at"] = 0

        with state_module.use_identity(send_as_id), \
             patch.object(concubine.random, "uniform", return_value=0):
            parsed = concubine._parse_voyage_text(text, now)
            self.assertTrue(concubine._apply_voyage_snapshot(parsed, now))

        self.assertEqual("sailing", state_module.state["concubine_voyage_status"])
        self.assertEqual(2, state_module.state["concubine_voyage_retry_count"])
        self.assertEqual(now + concubine.CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC, state_module.state["next_concubine_time"])

        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_phase"] = "voyage_return_pending"
            identity_state["concubine_voyage_msg_id"] = 918
            identity_state["concubine_voyage_retry_count"] = 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine.random, "uniform", return_value=30):
            handled = await concubine.handle_concubine_voyage_reply(
                text,
                now + 10,
                SimpleNamespace(raw_text=config.CMD_CONCUBINE_VOYAGE_RETURN, id=918),
                matched_family="concubine_voyage",
            )

        self.assertTrue(handled)
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual("idle", state_module.state["concubine_voyage_status"])
        self.assertEqual(0, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual(0, state_module.state["concubine_voyage_retry_count"])

    async def test_voyage_pending_timeout_retries_once_then_preserves_lock(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        first_retry = SimpleNamespace(id=919, sent_at=now)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "voyage_return_pending"
            identity_state["concubine_voyage_status"] = "returned"
            identity_state["concubine_voyage_msg_id"] = 918
            identity_state["concubine_voyage_return_at"] = now - 1
            identity_state["next_concubine_time"] = now - 1

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "_recover_concubine_pending_from_message_log", new=AsyncMock(return_value=False)), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=first_retry)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_awaited_once_with(config.CMD_CONCUBINE_VOYAGE_RETURN, track=False, priority="chain")
        self.assertEqual("voyage_return_pending", state_module.state["concubine_phase"])
        self.assertEqual(919, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual(1, state_module.state["concubine_voyage_retry_count"])
        self.assertEqual(now + config.CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC, state_module.state["next_concubine_time"])

        later = now + config.CONCUBINE_VOYAGE_REPLY_TIMEOUT_SEC + 1
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "_recover_concubine_pending_from_message_log", new=AsyncMock(return_value=False)), \
             patch.object(concubine, "send_audit_log", new=AsyncMock()), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            await concubine.run_concubine_scheduler(later)

        mock_send.assert_not_awaited()
        self.assertEqual("idle", state_module.state["concubine_phase"])
        self.assertEqual(0, state_module.state["concubine_voyage_msg_id"])
        self.assertEqual(2, state_module.state["concubine_voyage_retry_count"])
        self.assertEqual("returned", state_module.state["concubine_voyage_status"])
        self.assertEqual(later + concubine.CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC, state_module.state["next_concubine_time"])

    async def test_status_reply_ignored_during_voyage_pending(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        panel_text = (
            "你的道心侍妾: 【凌玉灵】 (状态: 随行中)\n"
            "情缘值: 320\n"
            "远航状态: 冒险航线进行中，剩余约 56 分钟。\n"
        )
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True
            identity_state["concubine_phase"] = "voyage_pending"
            identity_state["concubine_voyage_msg_id"] = 920

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state") as save_mock:
            handled = await concubine.handle_concubine_status_reply(
                panel_text,
                now,
                SimpleNamespace(raw_text=config.CMD_CONCUBINE_STATUS, id=1),
                matched_family="concubine_status",
                current_msg_id=2,
            )

        self.assertTrue(handled)
        save_mock.assert_not_called()
        self.assertEqual("voyage_pending", state_module.state["concubine_phase"])
        self.assertEqual(920, state_module.state["concubine_voyage_msg_id"])

    def test_passive_concubine_voyage_text_updates_voyage_snapshot(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=320, dream_due_at=now + 3600, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_voyage_enabled"] = True

        with state_module.use_identity(send_as_id), \
             patch.object(concubine.random, "uniform", return_value=0):
            changed = passive_inbox._apply_concubine_passive(
                "侍妾仍在远航途中，暂无法与你同梦寻图。",
                now,
                "concubine_dream",
            )

        self.assertTrue(changed)
        self.assertEqual("sailing", state_module.state["concubine_voyage_status"])
        self.assertEqual(0, state_module.state["concubine_voyage_return_at"])
        self.assertEqual(now + concubine.CONCUBINE_VOYAGE_UNKNOWN_RECHECK_SEC, state_module.state["next_concubine_time"])


if __name__ == "__main__":
    unittest.main()
