import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import state as state_module
from model.config import (
    CD_BUFFER_SEC,
    CMD_FORMATION_ASSIST,
    FORMATION_ASSIST_REPLY_TIMEOUT_SEC,
    FORMATION_RECOVERY_DELAY_SEC,
    FORMATION_SUCCESS_COOLDOWN_SEC,
)
from model.features import formation
from model.real_message_replay import get_real_message_text


FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "real_message_samples.json"


def real_text(sample_id):
    return get_real_message_text(FIXTURE_PATH, sample_id)


class FormationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        self._reset_state()

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    def _reset_state(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}
        state_module._meta_state["identity_account_map"] = {}
        state_module.set_formation_run_state({})

    def _prepare_identity(self, identity_id=7063348270, *, username="rexy1205", sect_name="星宫", account_id=1, enabled=True):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username=username, sect_name=sect_name, enabled=enabled)
        if account_id:
            state_module.set_identity_account(identity_id, account_id)
        return identity_id

    def _record_invite(self, now=1_700_000_000.0, message_id=7897745, chat_id=0):
        return formation.apply_formation_reply_snapshot(
            "",
            real_text("formation.invite.external_star_palace"),
            now,
            message_id=message_id,
            chat_id=chat_id,
        )

    def test_real_invite_records_external_invite_without_identity_pending(self):
        now = 1_700_000_000.0
        self._prepare_identity()

        handled = self._record_invite(now)

        self.assertTrue(handled)
        run_state = state_module.get_formation_run_state()
        invite = run_state["active_invites"]["7897745"]
        self.assertEqual(7897745, invite["msg_id"])
        self.assertEqual("@david", invite["owner_username"])
        self.assertEqual(now + 60, invite["expire_at"])
        with state_module.use_identity(7063348270):
            self.assertEqual(0, state_module.state["formation_pending_invite_msg_id"])
            self.assertEqual(0, state_module.state["formation_pending_assist_msg_id"])

    def test_local_invite_is_not_recorded_as_external_invite(self):
        now = 1_700_000_000.0
        self._prepare_identity(username="David")

        handled = self._record_invite(now)

        self.assertTrue(handled)
        self.assertEqual({}, state_module.get_formation_run_state().get("active_invites", {}))

    async def test_scheduler_schedules_then_sends_one_assist(self):
        now = 1_700_000_000.0
        identity_id = self._prepare_identity()
        self._record_invite(now, chat_id=-1001680975844)

        with state_module.use_identity(identity_id):
            state_module.state["formation_enabled"] = True
            state_module.state["next_formation_time"] = now - 1
            with (
                patch.object(formation.random, "uniform", return_value=0),
                patch.object(formation, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7897749))) as send_mock,
                patch.object(formation, "save_state"),
            ):
                await formation.run_formation_scheduler(now)
                send_mock.assert_not_awaited()

                await formation.run_formation_scheduler(now)
                send_mock.assert_awaited_once_with(
                    CMD_FORMATION_ASSIST,
                    track=False,
                    reply_to=7897745,
                    target_chat_id=-1001680975844,
                    send_as_id=identity_id,
                    priority="urgent_reactive",
                    source_module="周天星斗",
                )
                self.assertEqual(7897745, state_module.state["formation_pending_invite_msg_id"])
                self.assertEqual(7897749, state_module.state["formation_pending_assist_msg_id"])

                await formation.run_formation_scheduler(now + 1)
                self.assertEqual(1, send_mock.await_count)

        attempt = state_module.get_formation_run_state()["attempted_assists"][str(identity_id)]["7897745"]
        self.assertEqual("sent", attempt["status"])
        self.assertEqual(now + FORMATION_ASSIST_REPLY_TIMEOUT_SEC, attempt["reply_deadline_at"])

    async def test_scheduler_blocks_disabled_no_account_and_non_star_palace(self):
        cases = (
            ("disabled", {"sect_name": "星宫", "account_id": 1, "formation_enabled": False}),
            ("no_account", {"sect_name": "星宫", "account_id": 0, "formation_enabled": True}),
            ("non_star_palace", {"sect_name": "太一门", "account_id": 1, "formation_enabled": True}),
        )
        for case_name, options in cases:
            with self.subTest(case_name=case_name):
                self._reset_state()
                now = 1_700_000_000.0
                identity_id = self._prepare_identity(
                    sect_name=options["sect_name"],
                    account_id=options["account_id"],
                )
                self._record_invite(now)
                with state_module.use_identity(identity_id):
                    state_module.state["formation_enabled"] = options["formation_enabled"]
                    state_module.state["next_formation_time"] = now - 1
                    with (
                        patch.object(formation, "send_game_command", new=AsyncMock()) as send_mock,
                        patch.object(formation, "save_state"),
                    ):
                        await formation.run_formation_scheduler(now)

                    send_mock.assert_not_awaited()
                    self.assertEqual(0, state_module.state["formation_pending_assist_msg_id"])

    def test_success_edit_sets_cooldown_when_local_username_is_participant(self):
        now = 1_700_000_000.0
        identity_id = self._prepare_identity(username="rexy1205")
        state_module.set_formation_run_state({
            "active_invites": {
                "7897745": {
                    "msg_id": 7897745,
                    "owner_username": "@david",
                    "created_at": now - 10,
                    "expire_at": now + 50,
                    "status": "open",
                }
            },
            "attempted_assists": {
                str(identity_id): {
                    "7897745": {
                        "status": "sent",
                        "command_msg_id": 7897749,
                        "reply_deadline_at": now + FORMATION_ASSIST_REPLY_TIMEOUT_SEC,
                        "updated_at": now,
                    }
                }
            },
        })

        with state_module.use_identity(identity_id):
            state_module.state["formation_enabled"] = True
            state_module.state["formation_pending_invite_msg_id"] = 7897745
            state_module.state["formation_pending_assist_msg_id"] = 7897749
            state_module.state["last_formation_msg_id"] = 7897749

        handled = formation.apply_formation_reply_snapshot(
            ".助阵",
            real_text("formation.success.edit"),
            now,
            message_id=7897745,
            reply_to_msg_id=7897744,
        )

        self.assertTrue(handled)
        with state_module.use_identity(identity_id):
            self.assertEqual(0, state_module.state["formation_pending_invite_msg_id"])
            self.assertEqual(0, state_module.state["formation_pending_assist_msg_id"])
            self.assertEqual(now + FORMATION_SUCCESS_COOLDOWN_SEC, state_module.state["formation_cooldown_until"])
            self.assertEqual("冷却中", state_module.state["formation_last_action"])
            self.assertIn("布阵成功", state_module.state["formation_last_result"])
        run_state = state_module.get_formation_run_state()
        self.assertEqual({}, run_state.get("active_invites"))
        self.assertEqual({}, run_state.get("attempted_assists"))
        self.assertEqual([identity_id], run_state["last_success"]["identity_ids"])

    def test_success_edit_without_local_participant_only_clears_pending(self):
        now = 1_700_000_000.0
        identity_id = self._prepare_identity(username="localstar")
        state_module.set_formation_run_state({
            "active_invites": {
                "7897745": {
                    "msg_id": 7897745,
                    "owner_username": "@david",
                    "created_at": now - 10,
                    "expire_at": now + 50,
                    "status": "open",
                }
            },
            "attempted_assists": {
                str(identity_id): {
                    "7897745": {
                        "status": "sent",
                        "command_msg_id": 7897749,
                        "reply_deadline_at": now + FORMATION_ASSIST_REPLY_TIMEOUT_SEC,
                        "updated_at": now,
                    }
                }
            },
        })

        with state_module.use_identity(identity_id):
            state_module.state["formation_enabled"] = True
            state_module.state["formation_pending_invite_msg_id"] = 7897745
            state_module.state["formation_pending_assist_msg_id"] = 7897749
            state_module.state["last_formation_msg_id"] = 7897749

        handled = formation.apply_formation_reply_snapshot(
            ".助阵",
            "【周天星斗大阵-成】\n"
            "星光汇聚，大阵已成！\n"
            "参与者: @David, @otherstar\n"
            "所有参与者已获得【星力加持】BUFF，持续 6 小时，并进入 12 小时的冷却期！",
            now,
            message_id=7897745,
            reply_to_msg_id=7897744,
        )

        self.assertTrue(handled)
        with state_module.use_identity(identity_id):
            self.assertEqual(0, state_module.state["formation_pending_invite_msg_id"])
            self.assertEqual(0, state_module.state["formation_pending_assist_msg_id"])
            self.assertEqual(0, state_module.state["formation_cooldown_until"])
            self.assertEqual(0, state_module.state["formation_last_success_at"])
        run_state = state_module.get_formation_run_state()
        self.assertEqual([], run_state["last_success"]["identity_ids"])

    def test_assist_cooldown_reply_uses_real_wait_text(self):
        now = 1_700_000_000.0
        identity_id = self._prepare_identity()
        with state_module.use_identity(identity_id):
            state_module.state["formation_pending_invite_msg_id"] = 7897745
            state_module.state["formation_pending_assist_msg_id"] = 7897749

        handled = formation.apply_formation_reply_snapshot(
            ".助阵",
            "你刚刚参与过布阵，心神消耗巨大，请在 6小时32分钟47秒 后再次助阵。",
            now,
            reply_to_msg_id=7897749,
            message_id=7897750,
        )

        self.assertTrue(handled)
        with state_module.use_identity(identity_id):
            self.assertEqual(0, state_module.state["formation_pending_invite_msg_id"])
            self.assertEqual(0, state_module.state["formation_pending_assist_msg_id"])
            self.assertEqual(now + 23567 + CD_BUFFER_SEC, state_module.state["formation_cooldown_until"])
            self.assertEqual("助阵冷却", state_module.state["formation_last_result"])

    def test_invite_failure_clears_pending_and_sets_short_backoff(self):
        now = 1_700_000_000.0
        identity_id = self._prepare_identity()
        with state_module.use_identity(identity_id):
            state_module.state["formation_pending_invite_msg_id"] = 7897745
            state_module.state["formation_pending_assist_msg_id"] = 7897749

        handled = formation.apply_formation_reply_snapshot(
            ".助阵",
            "没有找到正在召集的大阵，或阵法已过期。",
            now,
            reply_to_msg_id=7897749,
            message_id=7897750,
        )

        self.assertTrue(handled)
        with state_module.use_identity(identity_id):
            self.assertEqual(0, state_module.state["formation_pending_invite_msg_id"])
            self.assertEqual(0, state_module.state["formation_pending_assist_msg_id"])
            self.assertEqual(now + FORMATION_RECOVERY_DELAY_SEC, state_module.state["next_formation_time"])
            self.assertEqual("助阵邀请已失效", state_module.state["formation_last_error"])

    async def test_scheduled_invite_expiry_and_sent_reply_timeout_clear_state(self):
        now = 1_700_000_000.0
        identity_id = self._prepare_identity()
        self._record_invite(now)

        with state_module.use_identity(identity_id):
            state_module.state["formation_enabled"] = True
            state_module.state["next_formation_time"] = now - 1
            with (
                patch.object(formation.random, "uniform", return_value=5),
                patch.object(formation, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(formation, "save_state"),
            ):
                await formation.run_formation_scheduler(now)
                await formation.run_formation_scheduler(now + 61)

            send_mock.assert_not_awaited()

        attempt = state_module.get_formation_run_state()["attempted_assists"][str(identity_id)]["7897745"]
        self.assertEqual("failed", attempt["status"])
        self.assertEqual("expired", attempt["reason"])

        self._reset_state()
        identity_id = self._prepare_identity()
        self._record_invite(now)
        with state_module.use_identity(identity_id):
            state_module.state["formation_enabled"] = True
            state_module.state["next_formation_time"] = now - 1
            with (
                patch.object(formation.random, "uniform", return_value=0),
                patch.object(formation, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7897749))),
                patch.object(formation, "save_state"),
            ):
                await formation.run_formation_scheduler(now)
                await formation.run_formation_scheduler(now)
                await formation.run_formation_scheduler(now + FORMATION_ASSIST_REPLY_TIMEOUT_SEC + 1)

            self.assertEqual(0, state_module.state["formation_pending_invite_msg_id"])
            self.assertEqual(0, state_module.state["formation_pending_assist_msg_id"])
            self.assertEqual("助阵回复超时", state_module.state["formation_last_error"])
            self.assertEqual(
                now + FORMATION_ASSIST_REPLY_TIMEOUT_SEC + 1 + FORMATION_RECOVERY_DELAY_SEC,
                state_module.state["next_formation_time"],
            )

    async def test_sent_reply_timeout_recovers_logged_success_edit(self):
        now = 1_700_000_000.0
        identity_id = self._prepare_identity(username="rexy1205")
        self._record_invite(now)
        with state_module.use_identity(identity_id):
            state_module.state["formation_enabled"] = True
            state_module.state["next_formation_time"] = now - 1
            with (
                patch.object(formation.random, "uniform", return_value=0),
                patch.object(formation, "send_game_command", new=AsyncMock(return_value=SimpleNamespace(id=7897749))),
                patch.object(formation, "save_state"),
            ):
                await formation.run_formation_scheduler(now)
                await formation.run_formation_scheduler(now)

        success_entry = {
            "event_type": "edit",
            "message_id": 7897745,
            "chat_id": state_module.get_game_group_id(),
            "reply_to_msg_id": 7897744,
            "text": real_text("formation.success.edit"),
            "ts_epoch": now + 10,
        }
        with state_module.use_identity(identity_id), patch.object(
            formation, "find_message_log_replies", return_value=[]
        ), patch.object(formation, "find_message_log_message", return_value=success_entry), patch.object(
            formation, "save_state"
        ):
            await formation.run_formation_scheduler(now + FORMATION_ASSIST_REPLY_TIMEOUT_SEC + 1)

            self.assertEqual(0, state_module.state["formation_pending_invite_msg_id"])
            self.assertEqual(0, state_module.state["formation_pending_assist_msg_id"])
            self.assertEqual(now + 10 + FORMATION_SUCCESS_COOLDOWN_SEC, state_module.state["formation_cooldown_until"])
            self.assertNotEqual("助阵回复超时", state_module.state["formation_last_error"])


if __name__ == "__main__":
    unittest.main()
