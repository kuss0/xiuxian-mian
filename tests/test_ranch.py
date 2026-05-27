import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.features import ranch


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class RanchTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    async def test_success_waits_for_return_broadcast_before_next_send(self):
        send_as_id = 8659059191
        now = 1000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="WalterWA2000")

        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True
            with (
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                handled = await ranch.handle_ranch_reply(
                    "【万兽奔腾】\n你打开万兽谷传送阵，灵兽四散放养。",
                    now,
                    SimpleNamespace(id=123, raw_text=".一键放养"),
                    matched_family="ranch",
                )

            self.assertTrue(handled)
            self.assertTrue(state_module.state["ranch_return_pending"])

            state_module.state["next_ranch_time"] = now
            with (
                patch.object(ranch, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                await ranch.run_ranch_scheduler(now + 1)
            send_mock.assert_not_awaited()

        with (
            patch.object(ranch, "send_audit_log", new=AsyncMock()),
            patch.object(ranch, "save_state"),
        ):
            handled_return = await ranch.handle_ranch_return_broadcast(
                "【灵兽归来】\n道友 @WalterWA2000 你放养的灵兽已自行归来。",
                now + 2,
                SimpleNamespace(id=456),
            )

        self.assertTrue(handled_return)
        with state_module.use_identity(send_as_id):
            self.assertFalse(state_module.state["ranch_return_pending"])
            self.assertEqual(456, state_module.state["ranch_return_seen_msg_id"])

    async def test_stale_return_wait_reprobes_instead_of_waiting_forever(self):
        send_as_id = 3711993781
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="xuruode3")
        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True
            state_module.state["ranch_return_pending"] = True
            state_module.state["ranch_return_wait_since"] = now - ranch.RANCH_RETURN_MAX_WAIT_SEC - 1
            state_module.state["next_ranch_time"] = now - 3600

            with (
                patch.object(ranch.random, "uniform", return_value=ranch.RANCH_RETURN_STALE_REPROBE_MIN_SEC),
                patch.object(ranch, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                await ranch.run_ranch_scheduler(now)

            send_mock.assert_not_awaited()
            self.assertFalse(state_module.state["ranch_return_pending"])
            self.assertEqual(now + ranch.RANCH_RETURN_STALE_REPROBE_MIN_SEC, state_module.state["next_ranch_time"])
            self.assertIn("归来广播等待超时", state_module.state["ranch_last_error"])

    async def test_no_idle_after_stale_reprobe_keeps_waiting_for_return(self):
        send_as_id = 3711993781
        now = 1_700_000_000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="xuruode3")
        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True
            state_module.state["ranch_return_pending"] = False
            state_module.state["ranch_last_error"] = "灵兽归来广播等待超时，等待起点=昨日"

            with (
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                handled = await ranch.handle_ranch_reply(
                    "你当前没有处于【休息中】的灵兽可供放养。",
                    now,
                    SimpleNamespace(id=123, raw_text=".一键放养"),
                    matched_family="ranch",
                )

            self.assertTrue(handled)
            self.assertTrue(state_module.state["ranch_return_pending"])
            self.assertEqual(now, state_module.state["ranch_return_wait_since"])
            self.assertEqual("无休息中灵兽，继续等待归来", state_module.state["ranch_last_result"])
            self.assertEqual("", state_module.state["ranch_last_error"])

    async def test_wrong_sect_variants_disable_ranch(self):
        send_as_id = 3800619925
        now = 1000.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="growrdick")
        with state_module.use_identity(send_as_id):
            state_module.state["ranch_enabled"] = True

            with (
                patch.object(ranch, "send_audit_log", new=AsyncMock()),
                patch.object(ranch, "save_state"),
            ):
                handled = await ranch.handle_ranch_reply(
                    "你并非万灵宗弟子，无法通晓御兽之术。",
                    now,
                    SimpleNamespace(id=123, raw_text=".一键放养"),
                    matched_family="ranch",
                )

            self.assertTrue(handled)
            self.assertFalse(state_module.state["ranch_enabled"])
            self.assertIn("并非万灵宗弟子", state_module.state["ranch_last_error"])
