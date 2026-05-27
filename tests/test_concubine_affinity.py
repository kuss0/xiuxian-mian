import atexit
import copy
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
from model.features import concubine


class ConcubineAffinityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))

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

    async def test_scheduler_calibrates_stale_panel_before_active_cooldown_command(self):
        now = 1_700_000_000.0
        send_as_id = self._prepare_identity(affinity=1000, dream_due_at=now - 1, tianji_due_at=now + 3600)
        with state_module.use_identity(send_as_id) as identity_state:
            identity_state["concubine_last_snapshot_at"] = now - concubine.CONCUBINE_ACTIVE_STATUS_MAX_AGE_SEC - 1

        sent_msg = SimpleNamespace(id=987, sent_at=now)
        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            await concubine.run_concubine_scheduler(now)

        mock_send.assert_awaited_once_with(config.CMD_CONCUBINE_STATUS, track=False)
        self.assertEqual("status_pending", state_module.state["concubine_phase"])
        self.assertEqual(987, state_module.state["concubine_status_msg_id"])

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

        self.assertEqual("concubine_status", runtime._get_special_tracked_message_family(state, 501))
        self.assertEqual("storage_bag", runtime._get_special_tracked_message_family(state, 601))
        self.assertEqual("concubine_gift", runtime._get_special_tracked_message_family(state, 701))
        self.assertEqual("concubine_tianji", runtime._get_special_tracked_message_family(state, 801))

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
             patch.object(concubine, "save_state") as save_mock:
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

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock(return_value=sent_msg)) as mock_send:
            sent = await concubine._send_heart_choice(now)

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

        with state_module.use_identity(send_as_id), \
             patch.object(concubine, "save_state"), \
             patch.object(concubine, "send_game_command", new=AsyncMock()) as mock_send:
            sent = await concubine._send_heart_choice(now)

        self.assertFalse(sent)
        mock_send.assert_not_awaited()
        self.assertEqual("heart_choice_reply_pending", state_module.state["concubine_phase"])
        self.assertEqual(now + 35, state_module.state["next_concubine_time"])
        self.assertIn("已发送 .稳", state_module.state["concubine_heart_last_error"])

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


if __name__ == "__main__":
    unittest.main()
