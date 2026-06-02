import copy
import json
import tempfile
from types import SimpleNamespace
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from model import runtime
from model import state as state_module
from model.config import CD_BUFFER_SEC, TAIYI_CYCLE_CD_SEC
from model.features import taiyi


class _StateIsolationMixin:
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state["identity_ids"] = []
        state_module._meta_state["identity_states"] = {}
        state_module._meta_state["send_as_profiles"] = {}

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()


class TaiyiFallbackTests(_StateIsolationMixin, unittest.IsolatedAsyncioTestCase):
    def _prepare_identity(self, phase, *, entered_at, node_name=""):
        send_as_id = 3943773722
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="myios17")
        with state_module.use_identity(send_as_id):
            state_module.state["taiyi_enabled"] = True
            state_module.state["taiyi_node_search_enabled"] = True
            state_module.state["taiyi_phase"] = phase
            state_module.state["taiyi_phase_entered_at"] = entered_at
            state_module.state["taiyi_pending_node_name"] = node_name
            state_module.state["taiyi_yindao_msg_id"] = 101 if phase == "yindao_pending" else 0
            state_module.state["taiyi_node_search_msg_id"] = 102 if phase == "search_pending" else 0
            state_module.state["taiyi_node_define_msg_id"] = 103 if phase == "define_pending" else 0
            state_module.state["next_taiyi_cycle_time"] = entered_at - 1
            state_module.state["taiyi_yindao_resend_count"] = 0
        return send_as_id

    def _inbox_summaries(self, inbox_mock):
        return [str(call.kwargs.get("summary") or "") for call in inbox_mock.call_args_list]

    async def test_yindao_timeout_with_send_evidence_schedules_one_fast_resend(self):
        now = 1_700_000_100.0
        entered_at = now - taiyi.TAIYI_REPLY_LOST_TIMEOUT_SEC - 1
        send_as_id = self._prepare_identity("yindao_pending", entered_at=entered_at)
        resend_sent_at = now + 3

        with state_module.use_identity(send_as_id):
            with (
                patch.object(taiyi.random, "uniform", return_value=2.5),
                patch.object(taiyi, "_has_yindao_send_evidence", return_value=True),
                patch.object(
                    taiyi,
                    "send_game_command",
                    new=AsyncMock(return_value=SimpleNamespace(id=202, sent_at=resend_sent_at)),
                ) as send_mock,
                patch.object(taiyi, "send_audit_log", new=AsyncMock()),
                patch.object(taiyi, "save_state"),
            ):
                await taiyi.run_taiyi_scheduler(now)

                send_mock.assert_not_awaited()
                self.assertEqual("idle", state_module.state["taiyi_phase"])
                self.assertEqual(0, state_module.state["taiyi_yindao_msg_id"])
                self.assertEqual(1, state_module.state["taiyi_yindao_resend_count"])
                self.assertEqual(now + 2.5, state_module.state["next_taiyi_cycle_time"])
                self.assertIn("已确认真实出站", state_module.state["taiyi_last_error"])

                await taiyi.run_taiyi_scheduler(now + 3)

                send_mock.assert_awaited_once_with(".引道 水", track=False)
                self.assertEqual("yindao_pending", state_module.state["taiyi_phase"])
                self.assertEqual(202, state_module.state["taiyi_yindao_msg_id"])
                self.assertEqual(1, state_module.state["taiyi_yindao_resend_count"])

                await taiyi.run_taiyi_scheduler(resend_sent_at + taiyi.TAIYI_REPLY_LOST_TIMEOUT_SEC + 1)

                send_mock.assert_awaited_once()
                self.assertEqual("idle", state_module.state["taiyi_phase"])
                self.assertEqual(0, state_module.state["taiyi_yindao_msg_id"])
                self.assertEqual(0, state_module.state["taiyi_yindao_resend_count"])

    async def test_yindao_presend_boundary_retries_short_without_normal_cd(self):
        now = 1_700_000_150.0
        entered_at = now - taiyi.TAIYI_REPLY_LOST_TIMEOUT_SEC - 1
        send_as_id = self._prepare_identity("yindao_pending", entered_at=entered_at)

        with state_module.use_identity(send_as_id):
            state_module.state["taiyi_yindao_msg_id"] = 0
            with (
                patch.object(taiyi.random, "uniform", return_value=90),
                patch.object(taiyi, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(taiyi, "send_audit_log", new=AsyncMock()),
                patch.object(taiyi, "save_state"),
            ):
                await taiyi.run_taiyi_scheduler(now)

                send_mock.assert_not_awaited()
                self.assertEqual("idle", state_module.state["taiyi_phase"])
                self.assertEqual(0, state_module.state["taiyi_yindao_msg_id"])
                self.assertEqual(1, state_module.state["taiyi_yindao_resend_count"])
                self.assertEqual(now + 90, state_module.state["next_taiyi_cycle_time"])
                self.assertIn("无真实出站记录", state_module.state["taiyi_last_error"])

    async def test_yindao_msg_id_without_send_evidence_retries_short(self):
        now = 1_700_000_160.0
        entered_at = now - taiyi.TAIYI_REPLY_LOST_TIMEOUT_SEC - 1
        send_as_id = self._prepare_identity("yindao_pending", entered_at=entered_at)

        with state_module.use_identity(send_as_id):
            state_module.state["taiyi_yindao_msg_id"] = 9446605
            with (
                patch.object(taiyi.random, "uniform", return_value=90),
                patch.object(taiyi, "_has_yindao_send_evidence", return_value=False),
                patch.object(taiyi, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(taiyi, "send_audit_log", new=AsyncMock()),
                patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
                patch.object(taiyi, "save_state"),
            ):
                await taiyi.run_taiyi_scheduler(now)

                send_mock.assert_not_awaited()
                self.assertEqual("idle", state_module.state["taiyi_phase"])
                self.assertEqual(0, state_module.state["taiyi_yindao_msg_id"])
                self.assertEqual(1, state_module.state["taiyi_yindao_resend_count"])
                self.assertEqual(now + 90, state_module.state["next_taiyi_cycle_time"])
                self.assertIn("无真实出站记录", state_module.state["taiyi_last_error"])
                summaries = self._inbox_summaries(inbox_mock)
                self.assertTrue(any("引道重试" in summary and "msg_id=9446605" in summary for summary in summaries))

    async def test_yindao_stale_msg_id_from_real_wrong_command_retries_short(self):
        send_as_id = 8659059191
        wrong_msg_id = 9446605
        entered_at = 1_779_846_597.0
        now = 1_779_846_658.0
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="WalterWA2000")
        with state_module.use_identity(send_as_id):
            state_module.state["taiyi_enabled"] = True
            state_module.state["taiyi_phase"] = "yindao_pending"
            state_module.state["taiyi_phase_entered_at"] = entered_at
            state_module.state["taiyi_pending_node_name"] = ""
            state_module.state["taiyi_yindao_msg_id"] = wrong_msg_id
            state_module.state["taiyi_node_search_msg_id"] = 0
            state_module.state["taiyi_node_define_msg_id"] = 0
            state_module.state["next_taiyi_cycle_time"] = now - 1

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2026-05-27.log"
            log_path.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "ts": "2026-05-27 09:50:01 UTC+8",
                            "event_type": "message",
                            "message_id": wrong_msg_id,
                            "chat_id": -1001680975844,
                            "sender_id": -1002232878184,
                            "topic_id": 0,
                            "reply_to_msg_id": 7310786,
                            "text": ".闯塔",
                        },
                        {
                            "ts": "2026-05-27 09:55:36 UTC+8",
                            "event_type": "message",
                            "message_id": 9446793,
                            "chat_id": -1001680975844,
                            "sender_id": send_as_id,
                            "topic_id": 0,
                            "reply_to_msg_id": 7310786,
                            "text": ".引道 水",
                        },
                        {
                            "ts": "2026-05-27 09:55:37 UTC+8",
                            "event_type": "message",
                            "message_id": 9446794,
                            "chat_id": -1001680975844,
                            "sender_id": 8349385938,
                            "topic_id": 7310786,
                            "reply_to_msg_id": 9446793,
                            "text": "你引动【水之道】，获得了 100点神识！\n并领悟了临时增益【润水之息】：\n普通闭关修炼时，获得的修为增加45%。",
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            with state_module.use_identity(send_as_id):
                with (
                    patch.object(taiyi, "MESSAGES_DIR", tmpdir),
                    patch.object(taiyi.random, "uniform", return_value=90),
                    patch.object(taiyi, "send_game_command", new=AsyncMock()) as send_mock,
                    patch.object(taiyi, "send_audit_log", new=AsyncMock()) as audit_mock,
                    patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
                    patch.object(taiyi, "save_state"),
                ):
                    await taiyi.run_taiyi_scheduler(now)

                    send_mock.assert_not_awaited()
                    audit_mock.assert_awaited_once()
                    self.assertIn(f"msg_id={wrong_msg_id}", audit_mock.await_args.args[0])
                    summaries = self._inbox_summaries(inbox_mock)
                    self.assertTrue(any("引道重试" in summary and f"msg_id={wrong_msg_id}" in summary for summary in summaries))
                    self.assertEqual("idle", state_module.state["taiyi_phase"])
                    self.assertEqual(0, state_module.state["taiyi_yindao_msg_id"])
                    self.assertEqual(1, state_module.state["taiyi_yindao_resend_count"])
                    self.assertEqual(now + 90, state_module.state["next_taiyi_cycle_time"])
                    self.assertIn("无真实出站记录", state_module.state["taiyi_last_error"])

    def test_yindao_send_evidence_requires_sent_log_entry(self):
        send_as_id = 8659059191
        msg_id = 9446793
        sent_at = 1_700_000_000.0
        now = sent_at + 61

        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "2023-11-15.log"
            log_path.write_text(
                "\n".join(
                    json.dumps(item, ensure_ascii=False)
                    for item in (
                        {
                            "ts": "2023-11-15 06:13:20 UTC+8",
                            "event_type": "message",
                            "message_id": msg_id,
                            "chat_id": -1001680975844,
                            "sender_id": send_as_id,
                            "reply_to_msg_id": 7310786,
                            "text": ".引道 水",
                        },
                        {
                            "ts": "2023-11-15 06:13:21 UTC+8",
                            "event_type": "message",
                            "message_id": msg_id,
                            "chat_id": -1001680975844,
                            "sender_id": -1008659059191,
                            "reply_to_msg_id": 7310786,
                            "text": ".引道 水",
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )

            with patch.object(taiyi, "MESSAGES_DIR", tmpdir):
                self.assertFalse(
                    taiyi._has_yindao_send_evidence(send_as_id, msg_id, ".引道 水", sent_at, now)
                )

            with log_path.open("a", encoding="utf-8") as f:
                f.write(
                    json.dumps(
                        {
                            "ts": "2023-11-15 06:13:22 UTC+8",
                            "event_type": "sent",
                            "message_id": msg_id,
                            "chat_id": -1001680975844,
                            "sender_id": send_as_id,
                            "reply_to_msg_id": 0,
                            "text": ".引道 水",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

            with patch.object(taiyi, "MESSAGES_DIR", tmpdir):
                self.assertTrue(
                    taiyi._has_yindao_send_evidence(send_as_id, msg_id, ".引道 水", sent_at, now)
                )

    async def test_yindao_scheduler_does_not_persist_pending_before_send_result(self):
        now = 1_700_000_175.0
        send_as_id = self._prepare_identity("idle", entered_at=0)

        async def fake_send(command, track=True):
            self.assertEqual(".引道 水", command)
            self.assertFalse(track)
            self.assertEqual("idle", state_module.state["taiyi_phase"])
            self.assertEqual(0, state_module.state["taiyi_yindao_msg_id"])
            return SimpleNamespace(id=555, sent_at=now + 1)

        with state_module.use_identity(send_as_id):
            state_module.state["next_taiyi_cycle_time"] = now - 1
            with (
                patch.object(taiyi, "send_game_command", new=fake_send),
                patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
                patch.object(taiyi, "save_state"),
            ):
                await taiyi.run_taiyi_scheduler(now)

                self.assertEqual("yindao_pending", state_module.state["taiyi_phase"])
                self.assertEqual(555, state_module.state["taiyi_yindao_msg_id"])
                self.assertEqual(now + 1, state_module.state["taiyi_phase_entered_at"])
                summaries = self._inbox_summaries(inbox_mock)
                self.assertTrue(any("引道已发送" in summary and "msg_id=555" in summary and ".引道 水" in summary for summary in summaries))

    async def test_search_timeout_falls_back_without_resend(self):
        now = 1_700_000_200.0
        entered_at = now - taiyi.TAIYI_REPLY_LOST_TIMEOUT_SEC - 1
        send_as_id = self._prepare_identity("search_pending", entered_at=entered_at)

        with state_module.use_identity(send_as_id):
            with (
                patch.object(taiyi, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(taiyi, "send_audit_log", new=AsyncMock()),
                patch.object(taiyi, "save_state"),
            ):
                await taiyi.run_taiyi_scheduler(now)

                send_mock.assert_not_awaited()
                self.assertEqual("idle", state_module.state["taiyi_phase"])
                self.assertEqual(0, state_module.state["taiyi_node_search_msg_id"])
                self.assertIn("搜寻节点", state_module.state["taiyi_last_error"])

    async def test_define_timeout_falls_back_without_resend(self):
        now = 1_700_000_300.0
        entered_at = now - taiyi.TAIYI_REPLY_LOST_TIMEOUT_SEC - 1
        send_as_id = self._prepare_identity("define_pending", entered_at=entered_at, node_name="空间节点·玄霜")

        with state_module.use_identity(send_as_id):
            with (
                patch.object(taiyi, "send_game_command", new=AsyncMock()) as send_mock,
                patch.object(taiyi, "send_audit_log", new=AsyncMock()),
                patch.object(taiyi, "save_state"),
            ):
                await taiyi.run_taiyi_scheduler(now)

                send_mock.assert_not_awaited()
                self.assertEqual("idle", state_module.state["taiyi_phase"])
                self.assertEqual("", state_module.state["taiyi_pending_node_name"])
                self.assertEqual(0, state_module.state["taiyi_node_define_msg_id"])

    async def test_late_manual_yindao_success_calibrates_cycle_without_search(self):
        now = 1_700_000_400.0
        send_as_id = self._prepare_identity("idle", entered_at=now - 300)
        reply_to = SimpleNamespace(id=999, raw_text=".引道 水")

        with state_module.use_identity(send_as_id):
            state_module.state["next_taiyi_cycle_time"] = now - 1
            with (
                patch.object(taiyi, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(taiyi, "_fire_and_forget") as fire_mock,
                patch("model.features.passive_inbox.record_passive_inbox_event") as inbox_mock,
                patch.object(taiyi, "save_state"),
            ):
                handled = await taiyi.handle_taiyi_yindao_reply(
                    "你引动【水之道】，获得了 100点神识！\n"
                    "并领悟了临时增益【润水之息】：\n"
                    "普通闭关修炼时，获得的修为增加45%。",
                    now,
                    reply_to,
                )

        self.assertTrue(handled)
        audit_mock.assert_awaited_once()
        fire_mock.assert_not_called()
        summaries = self._inbox_summaries(inbox_mock)
        self.assertTrue(any("引道手动/迟到成功" in summary for summary in summaries))
        self.assertTrue(any(
            call.kwargs.get("family") == "taiyi_yindao"
            and call.kwargs.get("decision") == "calibrate_manual_late_no_search"
            and "你引动【水之道】" in str(call.kwargs.get("matched_text") or "")
            for call in inbox_mock.call_args_list
        ))
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["taiyi_phase"])
            self.assertEqual(0, state_module.state["taiyi_yindao_msg_id"])
            self.assertEqual(now + TAIYI_CYCLE_CD_SEC + CD_BUFFER_SEC, state_module.state["next_taiyi_cycle_time"])

    async def test_manual_yindao_reply_sender_routes_and_calibrates_cycle(self):
        now = 1_700_000_500.0
        send_as_id = 8659059191
        state_module.ensure_identity_registered(send_as_id)
        state_module.update_send_as_profile(send_as_id, username="WalterWA2000")
        reply_to = SimpleNamespace(id=9446793, sender_id=send_as_id, raw_text=".引道 水")
        reply_context = runtime.get_reply_context(reply_to)

        self.assertEqual(send_as_id, reply_context["send_as_id"])
        self.assertEqual("taiyi_yindao", reply_context["family"])

        with state_module.use_identity(send_as_id):
            state_module.state["taiyi_enabled"] = True
            state_module.state["taiyi_node_search_enabled"] = False
            state_module.state["taiyi_phase"] = "idle"
            state_module.state["next_taiyi_cycle_time"] = now - 1
            with (
                patch.object(taiyi, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(taiyi, "save_state"),
            ):
                handled = await taiyi.handle_taiyi_yindao_reply(
                    "你引动【水之道】，获得了 100点神识！\n并领悟了临时增益【润水之息】：\n普通闭关修炼时，获得的修为增加45%。",
                    now,
                    reply_to,
                    matched_family=reply_context["family"],
                )

        self.assertTrue(handled)
        audit_mock.assert_awaited_once()
        with state_module.use_identity(send_as_id):
            self.assertEqual("idle", state_module.state["taiyi_phase"])
            self.assertEqual(now + TAIYI_CYCLE_CD_SEC + CD_BUFFER_SEC, state_module.state["next_taiyi_cycle_time"])


if __name__ == "__main__":
    unittest.main()
