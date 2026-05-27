import atexit
import asyncio
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

from model import state as state_module
from model.features import quiz


class QuizResultParsingTests(unittest.TestCase):
    def test_parse_new_quiz_result_titles(self):
        correct = quiz._parse_quiz_result("【玄骨考校·答对】\n@dao 的答案 B 完全正确")
        wrong = quiz._parse_quiz_result("【玄骨考校·答错】\n@dao 的答案 A 错了（正确答案：C）")
        timeout = quiz.RE_QUIZ_RESULT_TIMEOUT.search("【玄骨考校·超时】\n@dao 未在时间内作答")

        self.assertEqual("correct", correct["status"])
        self.assertEqual("B", correct["correct_answer"])
        self.assertEqual("wrong", wrong["status"])
        self.assertEqual("A", wrong["submitted_answer"])
        self.assertEqual("C", wrong["correct_answer"])
        self.assertIsNotNone(timeout)


class QuizButtonAnswerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(self._meta_state_snapshot)
        super().tearDown()

    async def test_click_quiz_answer_button_uses_matching_button(self):
        clicked = []

        class Message:
            buttons = [[SimpleNamespace(text="A"), SimpleNamespace(text="B")]]

            async def click(self, row_index, col_index):
                clicked.append((row_index, col_index))

        class Client:
            async def get_messages(self, chat_id, ids):
                self.seen = (chat_id, ids)
                return Message()

        client = Client()
        with patch.object(quiz, "_get_identity_client", return_value=client):
            ok, error = await quiz._click_quiz_answer_button(10001, -100, 123, "B")

        self.assertTrue(ok, error)
        self.assertEqual([(0, 1)], clicked)
        self.assertEqual((-100, 123), client.seen)

    async def test_button_failure_falls_back_to_command(self):
        sent_msg = SimpleNamespace(sent_at=1000)
        state_module.ensure_identity_registered(10001)
        with (
            state_module.use_identity(10001),
            patch.object(quiz, "_click_quiz_answer_button", AsyncMock(return_value=(False, "未找到按钮"))),
            patch.object(quiz, "_send_quiz_answer", AsyncMock(return_value=sent_msg)) as send_answer,
        ):
            ok, method, msg, error = await quiz._send_quiz_answer_with_fallback(10001, "C", 123, prefer_button=True)

        self.assertTrue(ok)
        self.assertEqual(quiz.QUIZ_ANSWER_METHOD_COMMAND, method)
        self.assertIs(msg, sent_msg)
        self.assertEqual("未找到按钮", error)
        send_answer.assert_awaited_once_with("C", 123)

    async def test_real_timeout_result_clears_pending_and_prevents_retry(self):
        identity_id = 10001
        question = "乾蓝冰焰在原著中最初是封附在何种通天灵宝之外？"
        options = {"A": "虚天鼎", "B": "掌天瓶", "C": "太乙青山", "D": "大五行幻世轮"}
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="li", enabled=True)
        state_module.set_quiz_learning_watchers({
            "li": {
                "target_tag": "@li",
                "identity_id": identity_id,
                "question": question,
                "options": options,
                "expire_at": 1_700_000_420.0,
                "matched_answer": "A",
            }
        })
        with state_module.use_identity(identity_id):
            state_module.state["quiz_enabled"] = True
            state_module.state["quiz_question"] = question
            state_module.state["quiz_options"] = options
            state_module.state["quiz_answer"] = "A"
            state_module.state["quiz_phase"] = quiz.QUIZ_PHASE_WAITING_RESULT
            state_module.state["quiz_reply_to_msg_id"] = 9451968
            state_module.state["quiz_chat_id"] = -1001680975844
            state_module.state["next_quiz_time"] = 1_700_000_060.0
            state_module.state["quiz_retry_count"] = 0

        audit_mock = AsyncMock()
        send_answer_mock = AsyncMock()
        with (
            patch.object(quiz, "send_audit_log", new=audit_mock),
            patch.object(quiz, "save_state"),
            patch.object(quiz, "save_quiz_learning_watchers_state"),
            patch.object(quiz, "_send_quiz_answer", new=send_answer_mock),
            patch.object(quiz, "_match_quiz_answer", return_value=("A", "exact_question")),
        ):
            handled = await quiz.handle_quiz_result_broadcast(
                "【玄骨考校·超时】\n"
                "@li 面对玄骨上人的提问，竟迟迟无法作答，被视为不堪造就。\n"
                "玄骨上人略感失望，一道神念冲击让你损失了 1000 点修为。",
                now=1_700_000_050.0,
            )
            with state_module.use_identity(identity_id):
                await quiz.run_quiz_scheduler(1_700_000_120.0)

        self.assertTrue(handled)
        self.assertEqual({}, state_module.get_quiz_learning_watchers())
        with state_module.use_identity(identity_id):
            self.assertEqual(0, state_module.state["quiz_reply_to_msg_id"])
            self.assertEqual("", state_module.state["quiz_phase"])
            self.assertEqual("收到玄骨考校超时结果，停止重试", state_module.state["quiz_last_error"])
        send_answer_mock.assert_not_awaited()
        self.assertIn("题库内超时未作答", audit_mock.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
