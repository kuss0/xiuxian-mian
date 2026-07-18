import atexit
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch


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
from model.features import tianji_quiz


class TianjiQuizTargetTests(unittest.IsolatedAsyncioTestCase):
    QUESTION = "一加一等于几？"
    OPTIONS = {"A": "一", "B": "二", "C": "三", "D": "四"}

    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        state_module.state["tianji_quiz_pending"] = {}
        self._tmpdir = tempfile.TemporaryDirectory()
        self._bank_patch = patch.object(
            tianji_quiz,
            "TIANJI_QUIZ_BANK_FILE",
            str(Path(self._tmpdir.name) / "tianji_quiz_bank.json"),
        )
        self._bank_patch.start()

    def tearDown(self):
        self._bank_patch.stop()
        self._tmpdir.cleanup()
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(self._meta_state_snapshot))
        super().tearDown()

    def _register_identity(self, identity_id=910001, username="local_user"):
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username=username, label=username)
        return identity_id

    def _quiz_prompt(self, target_line=""):
        lines = [
            "【天机考验】",
            target_line,
            "请在 2分钟 内，根据以下问题，直接回复本消息 给出你的答案：",
            "",
            self.QUESTION,
            f"A. {self.OPTIONS['A']}",
            f"B. {self.OPTIONS['B']}",
            f"C. {self.OPTIONS['C']}",
            f"D. {self.OPTIONS['D']}",
            "",
            "回答错误或超时，将被视为心怀叵测之徒，后果自负！",
        ]
        return "\n".join(line for line in lines if line)

    def _seed_known_answer(self):
        status, _ = tianji_quiz.save_tianji_quiz_bank_entry(self.QUESTION, self.OPTIONS, "B")
        self.assertEqual("added", status)

    def test_unknown_target_does_not_scan_whole_prompt_for_identity(self):
        self._register_identity(username="local_user")

        identity_id = tianji_quiz._find_target_identity_id("未知目标", "旁白提到 local_user")

        self.assertIsNone(identity_id)

    async def test_prompt_without_explicit_target_does_not_queue_known_answer(self):
        self._register_identity(username="local_user")
        self._seed_known_answer()

        with (
            patch.object(tianji_quiz, "_schedule_tianji_quiz_due_task") as schedule_mock,
            patch.object(tianji_quiz, "send_audit_log", new=AsyncMock()),
        ):
            handled = await tianji_quiz.handle_tianji_quiz_prompt(
                self._quiz_prompt("旁白提到 local_user，但没有明确 @ 目标"),
                now=1_700_000_000.0,
                event=SimpleNamespace(id=321, chat_id=-100),
            )

        self.assertTrue(handled)
        self.assertEqual({}, state_module.state["tianji_quiz_pending"])
        schedule_mock.assert_not_called()

    async def test_prompt_with_explicit_target_queues_known_answer_for_matching_identity(self):
        identity_id = self._register_identity(username="local_user")
        self._seed_known_answer()

        with (
            patch.object(tianji_quiz.random, "uniform", return_value=10.0),
            patch.object(tianji_quiz, "_schedule_tianji_quiz_due_task") as schedule_mock,
            patch.object(tianji_quiz, "send_audit_log", new=AsyncMock()),
            patch.object(tianji_quiz, "save_state"),
        ):
            handled = await tianji_quiz.handle_tianji_quiz_prompt(
                self._quiz_prompt("目标 @local_user"),
                now=1_700_000_000.0,
                event=SimpleNamespace(id=321, chat_id=-100),
            )

        self.assertTrue(handled)
        pending = state_module.state["tianji_quiz_pending"]
        self.assertEqual(["-100:321"], list(pending.keys()))
        self.assertEqual(identity_id, pending["-100:321"]["identity_id"])
        self.assertEqual("B", pending["-100:321"]["answer"])
        self.assertEqual(1_700_000_010.0, pending["-100:321"]["due_at"])
        schedule_mock.assert_called_once_with(1_700_000_010.0)

    async def test_duplicate_prompt_event_does_not_queue_twice(self):
        self._register_identity(username="local_user")
        self._seed_known_answer()
        event = SimpleNamespace(id=321, chat_id=-100)
        text = self._quiz_prompt("@local_user 道友，天机阁长老发现你近期气息异常，特降下考验以辨明正身！")

        with (
            patch.object(tianji_quiz.random, "uniform", return_value=10.0),
            patch.object(tianji_quiz, "_schedule_tianji_quiz_due_task") as schedule_mock,
            patch.object(tianji_quiz, "send_audit_log", new=AsyncMock()),
            patch.object(tianji_quiz, "save_state"),
        ):
            first = await tianji_quiz.handle_tianji_quiz_prompt(text, now=1_700_000_000.0, event=event)
            second = await tianji_quiz.handle_tianji_quiz_prompt(text, now=1_700_000_001.0, event=event)

        self.assertTrue(first)
        self.assertTrue(second)
        self.assertEqual(["-100:321"], list(state_module.state["tianji_quiz_pending"].keys()))
        schedule_mock.assert_called_once_with(1_700_000_010.0)

    def test_schedule_tianji_quiz_due_task_does_not_create_sleep_task(self):
        def close_coro(coro):
            if hasattr(coro, "close"):
                coro.close()

        fire_mock = Mock(side_effect=close_coro)

        with patch.object(tianji_quiz, "_fire_and_forget", new=fire_mock, create=True):
            result = tianji_quiz._schedule_tianji_quiz_due_task(1_700_000_010.0)

        self.assertIsNone(result)
        fire_mock.assert_not_called()

    async def test_scheduler_sends_answer_with_intent_metadata(self):
        identity_id = self._register_identity(username="local_user")
        now = 1_700_000_010.0
        state_module.state["tianji_quiz_pending"] = {
            "-100:321": {
                "target": "@local_user",
                "identity_id": identity_id,
                "question": self.QUESTION,
                "options": self.OPTIONS,
                "answer": "B",
                "due_at": now - 1,
                "deadline_at": now + 120,
                "created_at": now - 10,
                "msg_id": 321,
                "chat_id": -100,
                "retry_count": 0,
                "phase": "queued",
                "sent_msg_id": 0,
                "result_due_at": 0,
            }
        }
        send_mock = AsyncMock(return_value=SimpleNamespace(id=555, sent_at=now))

        with (
            patch.object(tianji_quiz, "send_game_command", new=send_mock),
            patch.object(tianji_quiz, "send_audit_log", new=AsyncMock()),
            patch.object(tianji_quiz, "save_state"),
        ):
            await tianji_quiz.run_tianji_quiz_scheduler(now)

        send_mock.assert_awaited_once_with(
            "B",
            track=False,
            reply_to=321,
            send_as_id=identity_id,
            priority="p0",
            source_module="天机考验",
            op_id="tianji_quiz:-100:321:answer",
            chain_id="tianji_quiz:-100:321",
        )
        item = state_module.state["tianji_quiz_pending"]["-100:321"]
        self.assertEqual("waiting_result", item["phase"])
        self.assertEqual(555, item["sent_msg_id"])
        self.assertEqual(now + tianji_quiz.TIANJI_QUIZ_RESULT_TIMEOUT_SEC, item["due_at"])

    async def test_real_timeout_result_reply_clears_pending_with_terminal_label(self):
        state_module.state["tianji_quiz_pending"] = {
            "-100:321": {
                "target": "@local_user",
                "identity_id": 910001,
                "question": self.QUESTION,
                "options": self.OPTIONS,
                "answer": "B",
                "due_at": 1_700_000_030.0,
                "deadline_at": 1_700_000_120.0,
                "created_at": 1_700_000_000.0,
                "msg_id": 321,
                "chat_id": -100,
                "retry_count": 0,
                "phase": "waiting_result",
                "sent_msg_id": 555,
                "sent_at": 1_700_000_010.0,
                "result_due_at": 1_700_000_030.0,
            }
        }
        event = SimpleNamespace(reply_to=SimpleNamespace(reply_to_msg_id=555))
        audit_mock = AsyncMock()

        with patch.object(tianji_quiz, "send_audit_log", new=audit_mock), patch.object(tianji_quiz, "save_state"):
            handled = await tianji_quiz.handle_tianji_quiz_result_broadcast(
                "考验超时！你的嫌疑更重了！天道将降下惩戒！",
                now=1_700_000_020.0,
                event=event,
            )

        self.assertTrue(handled)
        self.assertEqual({}, state_module.state["tianji_quiz_pending"])
        audit_text = audit_mock.await_args.args[0]
        self.assertIn("考验超时", audit_text)

    async def test_waiting_result_recovers_logged_reply_before_retry(self):
        now = 1_700_000_040.0
        state_module.state["tianji_quiz_pending"] = {
            "-100:321": {
                "target": "@local_user",
                "identity_id": 910001,
                "question": self.QUESTION,
                "options": self.OPTIONS,
                "answer": "B",
                "due_at": now - 1,
                "deadline_at": now + 80,
                "created_at": now - 40,
                "msg_id": 321,
                "chat_id": -100,
                "retry_count": 0,
                "phase": "waiting_result",
                "sent_msg_id": 555,
                "sent_at": now - 20,
                "result_due_at": now - 1,
            }
        }
        logged_result = {
            "event_type": "message",
            "message_id": 556,
            "chat_id": -100,
            "reply_to_msg_id": 555,
            "text": "考验通过！你的气息已恢复正常。",
        }

        with patch.object(
            tianji_quiz,
            "iter_message_log_entries_between",
            return_value=iter([(logged_result, now - 5)]),
        ), patch.object(tianji_quiz, "send_game_command", new=AsyncMock()) as send_mock, patch.object(
            tianji_quiz, "send_audit_log", new=AsyncMock()
        ), patch.object(tianji_quiz, "save_state"):
            await tianji_quiz.run_tianji_quiz_scheduler(now)

        self.assertEqual({}, state_module.state["tianji_quiz_pending"])
        send_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
