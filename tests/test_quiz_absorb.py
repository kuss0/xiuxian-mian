import atexit
import asyncio
import copy
import json
import sys
import tempfile
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
from model import ui
from model.features import quiz
from model.features import quiz_ai


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

    def test_parse_special_quiz_result_titles(self):
        for title in ("玄骨窥鼎", "玄骨夺焰"):
            with self.subTest(title=title):
                correct = quiz._parse_quiz_result(f"【{title}·答对】\n@WalterWA2000 的答案 C 完全正确！")
                wrong = quiz._parse_quiz_result(f"【{title}·答错】\n@WalterWA2000 的答案 A 错了（正确答案：C）")
                timeout = quiz.RE_QUIZ_RESULT_TIMEOUT.search(f"【{title}·超时】\n@WalterWA2000 未在时间内作答")
                self.assertEqual("correct", correct["status"])
                self.assertEqual("C", correct["correct_answer"])
                self.assertEqual("wrong", wrong["status"])
                self.assertEqual("C", wrong["correct_answer"])
                self.assertIsNotNone(timeout)


class QuizAiVoteTests(unittest.TestCase):
    def test_single_usable_provider_wins(self):
        result = quiz_ai._select_quiz_ai_vote([
            {"ok": False, "error": "timeout", "elapsed_ms": 2000, "label": "slow"},
            {"ok": True, "answer": "C", "confidence": 0.72, "elapsed_ms": 900, "label": "one"},
        ])

        self.assertTrue(result["ok"])
        self.assertEqual("C", result["answer"])
        self.assertEqual("C:1", result["vote_summary"])

    def test_majority_wins_over_fastest_minor_answer(self):
        result = quiz_ai._select_quiz_ai_vote([
            {"ok": True, "answer": "A", "confidence": 0.86, "elapsed_ms": 1200, "label": "a1"},
            {"ok": True, "answer": "B", "confidence": 0.99, "elapsed_ms": 200, "label": "b1"},
            {"ok": True, "answer": "A", "confidence": 0.91, "elapsed_ms": 900, "label": "a2"},
        ])

        self.assertTrue(result["ok"])
        self.assertEqual("A", result["answer"])
        self.assertEqual("A:2/B:1", result["vote_summary"])
        self.assertEqual(0.91, result["confidence"])

    def test_tie_uses_fastest_usable_answer(self):
        result = quiz_ai._select_quiz_ai_vote([
            {"ok": True, "answer": "A", "confidence": 0.91, "elapsed_ms": 800, "label": "a1"},
            {"ok": True, "answer": "B", "confidence": 0.89, "elapsed_ms": 300, "label": "b1"},
        ])

        self.assertTrue(result["ok"])
        self.assertEqual("B", result["answer"])

    def test_quiz_ai_is_http_only_and_does_not_use_local_ai_cli(self):
        source = (PROJECT_ROOT / "model" / "features" / "quiz_ai.py").read_text(encoding="utf-8")

        for forbidden in ("subprocess", "os.system", "Popen", ".codex", ".claude"):
            self.assertNotIn(forbidden, source)

    def test_extract_model_items_supports_openai_and_claude_shapes(self):
        openai_models = quiz_ai._extract_model_items({
            "data": [
                {"id": "gpt-5-mini"},
                {"id": "gpt-5-mini"},
                {"id": "gpt-5"},
            ]
        })
        claude_models = quiz_ai._extract_model_items({
            "data": [
                {"id": "claude-sonnet-4-5", "display_name": "Claude Sonnet 4.5"},
            ]
        })

        self.assertEqual(["gpt-5-mini", "gpt-5"], [item["id"] for item in openai_models])
        self.assertEqual("Claude Sonnet 4.5", claude_models[0]["label"])


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

    async def test_queued_answer_near_deadline_does_not_send_late(self):
        identity_id = 10001
        now = 1_700_000_100.0
        question = "韩立在内殿对付玄骨时，最能克制魔修的手段是什么？"
        options = {"A": "辟邪神雷", "B": "青竹蜂云剑", "C": "虚天鼎", "D": "掌天瓶"}
        state_module.ensure_identity_registered(identity_id)
        state_module.update_send_as_profile(identity_id, username="li", enabled=True)
        with state_module.use_identity(identity_id):
            state_module.state["quiz_enabled"] = True
            state_module.state["quiz_question"] = question
            state_module.state["quiz_options"] = options
            state_module.state["quiz_answer"] = "A"
            state_module.state["quiz_phase"] = quiz.QUIZ_PHASE_QUEUED_ANSWER
            state_module.state["quiz_reply_to_msg_id"] = 9451968
            state_module.state["quiz_chat_id"] = -1001680975844
            state_module.state["next_quiz_time"] = now - 1
            state_module.state["quiz_deadline_at"] = now + 3
            state_module.state["quiz_retry_count"] = 0

            send_answer_mock = AsyncMock()
            audit_mock = AsyncMock()
            with (
                patch.object(quiz, "_send_quiz_answer", new=send_answer_mock),
                patch.object(quiz, "_click_quiz_answer_button", new=AsyncMock(return_value=(False, "late"))),
                patch.object(quiz, "send_audit_log", new=audit_mock),
                patch.object(quiz, "save_state"),
            ):
                await quiz.run_quiz_scheduler(now)

            self.assertEqual(0, state_module.state["quiz_reply_to_msg_id"])
            self.assertEqual("", state_module.state["quiz_phase"])
            self.assertEqual("题目已过安全作答窗口", state_module.state["quiz_last_error"])

        send_answer_mock.assert_not_awaited()
        self.assertIn("安全作答窗口", audit_mock.await_args.args[0])

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

    async def test_confirmation_timeout_recovers_logged_result_before_retry(self):
        identity_id = 10001
        now = 1_700_000_120.0
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
                "expire_at": now + 300,
                "matched_answer": "A",
            }
        })
        logged_result = {
            "event_type": "edit",
            "message_id": 9451970,
            "chat_id": -1001680975844,
            "reply_to_msg_id": 0,
            "text": "【玄骨夺焰·答对】\n@li 的答案 A 完全正确！",
        }
        with state_module.use_identity(identity_id):
            pending_state = {
                "quiz_enabled": True,
                "quiz_question": question,
                "quiz_options": options,
                "quiz_answer": "A",
                "quiz_phase": quiz.QUIZ_PHASE_WAITING_RESULT,
                "quiz_reply_to_msg_id": 9451968,
                "quiz_chat_id": -1001680975844,
                "next_quiz_time": now - 1,
                "quiz_retry_count": 0,
            }
            for key, value in pending_state.items():
                state_module.state[key] = value
            with patch.object(
                quiz,
                "iter_message_log_entries_between",
                return_value=iter([(logged_result, now - 10)]),
            ), patch.object(quiz, "send_audit_log", new=AsyncMock()), patch.object(
                quiz, "save_state"
            ), patch.object(quiz, "save_quiz_learning_watchers_state"), patch.object(
                quiz, "_send_quiz_answer", new=AsyncMock()
            ) as send_mock, patch.object(quiz, "_match_quiz_answer", return_value=("A", "exact_question")):
                await quiz.run_quiz_scheduler(now)

            self.assertEqual(0, state_module.state["quiz_reply_to_msg_id"])
            self.assertEqual("", state_module.state["quiz_phase"])
            send_mock.assert_not_awaited()

    async def test_external_bank_timeout_logs_learning_only(self):
        question = "韩立能把虚天鼎从乾蓝冰焰池中拉出的关键倚仗是什么？"
        options = {"A": "血玉蜘蛛", "B": "啼魂兽", "C": "风雷翅", "D": "玄骨魔幡"}
        state_module.set_quiz_learning_watchers({
            "outerdao": {
                "target_tag": "@outerdao",
                "identity_id": None,
                "question": question,
                "options": options,
                "expire_at": 1_700_000_420.0,
                "matched_answer": "A",
            }
        })

        audit_mock = AsyncMock()
        with (
            patch.object(quiz, "send_audit_log", new=audit_mock),
            patch.object(quiz, "save_quiz_learning_watchers_state"),
            patch.object(quiz, "_match_quiz_answer", return_value=("A", "exact_question")),
        ):
            handled = await quiz.handle_quiz_result_broadcast(
                "【玄骨考校·超时】\n"
                "@outerdao 面对玄骨上人的提问，竟迟迟无法作答，被视为不堪造就。",
                now=1_700_000_050.0,
            )

        self.assertTrue(handled)
        self.assertEqual({}, state_module.get_quiz_learning_watchers())
        audit_text = audit_mock.await_args.args[0]
        self.assertIn("外部题库题目超时", audit_text)
        self.assertIn("未托管，仅学习观察", audit_text)
        self.assertNotIn("题库内超时未作答", audit_text)


class QuizAiAssistTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        self.identity_id = 10001
        state_module.ensure_identity_registered(self.identity_id)
        state_module.update_send_as_profile(self.identity_id, username="dao", enabled=True)
        quiz._reset_quiz_bank_cache()

    def tearDown(self):
        quiz._reset_quiz_bank_cache()
        state_module._meta_state.clear()
        state_module._meta_state.update(self._meta_state_snapshot)
        super().tearDown()

    def _prompt(self):
        return (
            "一缕魔念直逼你识海中的乾蓝寒焰，玄骨上人的声音在 @dao 脑海中炸响：\n"
            "“玄骨上人的化焰思路，核心在于先压住什么风险？”\n"
            "A. 灵根反噬\n"
            "B. 鼎焰反噬\n"
            "C. 天劫锁定\n"
            "D. 妖丹爆裂\n"
            "小辈，你有 300秒 的时间，可直接点击下方按钮作答，也可回复本消息并使用 .作答 <选项> 给出你的答案。"
        )

    async def test_ai_shadow_mode_records_suggestion_without_queueing_answer(self):
        state_module.set_quiz_ai_config({
            "enabled": True,
            "auto_answer_enabled": False,
            "provider": "codex",
            "model": "test-model",
            "confidence_threshold": 0.8,
        })
        with state_module.use_identity(self.identity_id):
            state_module.state["quiz_enabled"] = True
            with (
                patch.object(quiz, "_match_quiz_answer", return_value=("", "")),
                patch.object(quiz, "suggest_quiz_answer_multi", new=AsyncMock(return_value={
                    "ok": True,
                    "answer": "B",
                    "confidence": 0.91,
                    "reason": "题面指向鼎焰",
                    "provider": "codex",
                })) as suggest_mock,
                patch.object(quiz, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(quiz, "save_state"),
                patch.object(quiz, "save_quiz_ai_config_state"),
            ):
                handled = await quiz.handle_quiz_prompt(self._prompt(), 1_700_000_000.0, SimpleNamespace(id=123, chat_id=-100))

            self.assertTrue(handled)
            self.assertEqual("", state_module.state["quiz_answer"])
            self.assertEqual("", state_module.state["quiz_phase"])
            self.assertEqual("题库未命中，AI仅建议", state_module.state["quiz_last_error"])
            suggest_mock.assert_awaited_once()
            self.assertIn("AI建议", audit_mock.await_args.args[0])

    async def test_ai_auto_mode_queues_answer_when_confidence_passes_threshold(self):
        state_module.set_quiz_ai_config({
            "enabled": True,
            "auto_answer_enabled": True,
            "provider": "claude",
            "model": "test-model",
            "confidence_threshold": 0.8,
        })
        with state_module.use_identity(self.identity_id):
            state_module.state["quiz_enabled"] = True
            with (
                patch.object(quiz, "_match_quiz_answer", return_value=("", "")),
                patch.object(quiz, "suggest_quiz_answer_multi", new=AsyncMock(return_value={
                    "ok": True,
                    "answer": "B",
                    "confidence": 0.93,
                    "reason": "题面指向鼎焰",
                    "provider": "claude",
                })),
                patch.object(quiz.random, "uniform", return_value=25.0),
                patch.object(quiz, "send_audit_log", new=AsyncMock()) as audit_mock,
                patch.object(quiz, "save_state"),
                patch.object(quiz, "save_quiz_ai_config_state"),
            ):
                handled = await quiz.handle_quiz_prompt(self._prompt(), 1_700_000_000.0, SimpleNamespace(id=456, chat_id=-100))

            self.assertTrue(handled)
            self.assertEqual("B", state_module.state["quiz_answer"])
            self.assertEqual(quiz.QUIZ_PHASE_QUEUED_ANSWER, state_module.state["quiz_phase"])
            self.assertEqual("pending_button_ai", state_module.state["quiz_answer_method"])
            self.assertEqual("ai:claude:0.93", state_module.state["quiz_match_mode"])
            self.assertEqual(1_700_000_025.0, state_module.state["next_quiz_time"])
            self.assertEqual(1_700_000_300.0, state_module.state["quiz_deadline_at"])
            self.assertIn("AI已排队作答", audit_mock.await_args.args[0])

    async def test_global_pause_skips_ai_and_does_not_queue_auto_answer(self):
        state_module.set_quiz_ai_config({
            "enabled": True,
            "auto_answer_enabled": True,
            "provider": "claude",
            "model": "test-model",
            "confidence_threshold": 0.8,
        })
        with state_module.use_identity(self.identity_id):
            state_module.state["quiz_enabled"] = True
            with (
                patch.object(quiz, "get_global_enabled", return_value=False),
                patch.object(quiz, "suggest_quiz_answer_multi", new=AsyncMock()) as suggest_mock,
                patch.object(quiz, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                handled = await quiz.handle_quiz_prompt(self._prompt(), 1_700_000_000.0, SimpleNamespace(id=457, chat_id=-100))

            self.assertFalse(handled)
            self.assertEqual("", state_module.state["quiz_answer"])
            self.assertEqual("", state_module.state["quiz_phase"])
            suggest_mock.assert_not_awaited()
            audit_mock.assert_not_awaited()

    async def test_global_pause_clears_queued_answer_before_any_send(self):
        now = 1_700_000_100.0
        with state_module.use_identity(self.identity_id):
            state_module.state["quiz_enabled"] = True
            state_module.state["quiz_question"] = "暂停测试题"
            state_module.state["quiz_options"] = {"A": "甲", "B": "乙"}
            state_module.state["quiz_answer"] = "A"
            state_module.state["quiz_phase"] = quiz.QUIZ_PHASE_QUEUED_ANSWER
            state_module.state["quiz_reply_to_msg_id"] = 999
            state_module.state["next_quiz_time"] = now - 1
            with (
                patch.object(quiz, "get_global_enabled", return_value=False),
                patch.object(quiz, "_send_quiz_answer_with_fallback", new=AsyncMock()) as send_mock,
                patch.object(quiz, "save_state"),
            ):
                await quiz.run_quiz_scheduler(now)

            self.assertEqual(0, state_module.state["quiz_reply_to_msg_id"])
            self.assertEqual("", state_module.state["quiz_phase"])
            self.assertEqual("全局暂停，已取消自动作答", state_module.state["quiz_last_error"])
            send_mock.assert_not_awaited()

    async def test_ai_auto_mode_caps_delay_to_safety_window_after_ai_wait(self):
        state_module.set_quiz_ai_config({
            "enabled": True,
            "auto_answer_enabled": True,
            "provider": "codex",
            "model": "test-model",
            "confidence_threshold": 0.8,
            "decision_timeout_sec": 20,
            "answer_safety_margin_sec": 12,
        })
        prompt = self._prompt().replace("300秒", "30秒")
        with state_module.use_identity(self.identity_id):
            state_module.state["quiz_enabled"] = True
            with (
                patch.object(quiz, "_match_quiz_answer", return_value=("", "")),
                patch.object(quiz, "suggest_quiz_answer_multi", new=AsyncMock(return_value={
                    "ok": True,
                    "answer": "B",
                    "confidence": 0.93,
                    "reason": "题面指向鼎焰",
                    "provider": "vote:B:1",
                    "provider_count": 1,
                    "valid_count": 1,
                    "vote_summary": "B:1",
                })) as suggest_mock,
                patch.object(quiz.random, "uniform", return_value=25.0),
                patch.object(quiz.time, "time", side_effect=[1_000.0, 1_010.0, 1_010.0]),
                patch.object(quiz, "send_audit_log", new=AsyncMock()),
                patch.object(quiz, "save_state"),
                patch.object(quiz, "save_quiz_ai_config_state"),
            ):
                handled = await quiz.handle_quiz_prompt(prompt, 1_700_000_000.0, SimpleNamespace(id=789, chat_id=-100))

            self.assertTrue(handled)
            self.assertEqual("B", state_module.state["quiz_answer"])
            self.assertEqual(1_700_000_018.0, state_module.state["next_quiz_time"])
            self.assertEqual(1_700_000_030.0, state_module.state["quiz_deadline_at"])
            self.assertEqual(17.0, suggest_mock.await_args.kwargs["decision_timeout_sec"])


class QuizAiUiSnapshotTests(unittest.TestCase):
    def setUp(self):
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(self._meta_state_snapshot)

    def test_quiz_ai_snapshot_does_not_expose_api_key(self):
        state_module.set_quiz_ai_config({
            "enabled": True,
            "provider": "codex",
            "model": "test-model",
            "api_key": "secret-token",
        })

        snapshot = ui.get_quiz_ai_snapshot()

        self.assertTrue(snapshot["api_key_configured"])
        self.assertNotIn("api_key", snapshot)

    def test_quiz_ai_snapshot_does_not_expose_provider_api_keys(self):
        state_module.set_quiz_ai_config({
            "enabled": True,
            "providers": [
                {
                    "id": "ai1",
                    "enabled": True,
                    "label": "primary",
                    "provider": "codex",
                    "model": "test-model",
                    "api_key": "secret-token",
                }
            ],
        })

        snapshot = ui.get_quiz_ai_snapshot()

        self.assertTrue(snapshot["providers"][0]["api_key_configured"])
        self.assertNotIn("api_key", snapshot["providers"][0])
        self.assertEqual(5, len(snapshot["providers"]))

    def test_ui_set_quiz_ai_config_preserves_and_clears_provider_keys(self):
        state_module.set_quiz_ai_config({
            "enabled": True,
            "providers": [
                {
                    "id": "ai1",
                    "enabled": True,
                    "label": "primary",
                    "provider": "codex",
                    "model": "old-model",
                    "api_key": "secret-token",
                }
            ],
        })

        ok, _ = ui.ui_set_quiz_ai_config({
            "enabled": True,
            "auto_answer_enabled": True,
            "providers": [
                {
                    "id": "ai1",
                    "enabled": True,
                    "label": "primary",
                    "provider": "codex",
                    "model": "new-model",
                    "api_key": "",
                    "clear_api_key": False,
                    "timeout_sec": 10,
                    "temperature": 0,
                }
            ],
        })

        self.assertTrue(ok)
        self.assertEqual("secret-token", state_module.get_quiz_ai_config()["providers"][0]["api_key"])

        ok, _ = ui.ui_set_quiz_ai_config({
            "enabled": True,
            "providers": [
                {
                    "id": "ai1",
                    "enabled": True,
                    "label": "primary",
                    "provider": "codex",
                    "model": "new-model",
                    "clear_api_key": True,
                    "timeout_sec": 10,
                    "temperature": 0,
                }
            ],
        })

        self.assertTrue(ok)
        self.assertEqual("", state_module.get_quiz_ai_config()["providers"][0]["api_key"])


class QuizAiUiModelFetchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))

    def tearDown(self):
        state_module._meta_state.clear()
        state_module._meta_state.update(self._meta_state_snapshot)
        super().tearDown()

    async def test_ui_fetch_quiz_ai_models_preserves_saved_key_when_input_blank(self):
        state_module.set_quiz_ai_config({
            "enabled": True,
            "providers": [
                {
                    "id": "ai1",
                    "enabled": True,
                    "label": "primary",
                    "provider": "codex",
                    "model": "old-model",
                    "api_key": "secret-token",
                    "timeout_sec": 10,
                }
            ],
        })

        async def fake_list_models(provider_config):
            self.assertEqual("secret-token", provider_config["api_key"])
            self.assertEqual("codex", provider_config["provider"])
            return {
                "ok": True,
                "models": [{"id": "gpt-5-mini", "label": "gpt-5-mini"}],
                "provider": "codex",
                "label": "primary",
                "elapsed_ms": 12,
            }

        with patch.object(ui, "list_quiz_ai_models", new=AsyncMock(side_effect=fake_list_models)):
            ok, message, payload = await ui.ui_fetch_quiz_ai_models({
                "index": 0,
                "provider_config": {
                    "id": "ai1",
                    "enabled": True,
                    "label": "primary",
                    "provider": "codex",
                    "model": "",
                    "api_key": "",
                    "clear_api_key": False,
                    "timeout_sec": 10,
                    "temperature": 0,
                },
            })

        self.assertTrue(ok)
        self.assertIn("已获取 1 个模型", message)
        self.assertEqual("gpt-5-mini", payload["models"][0]["id"])


class QuizPassiveLearningTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self._meta_state_snapshot = copy.deepcopy(state_module._meta_state)
        state_module._meta_state.clear()
        state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
        quiz._reset_quiz_bank_cache()

    def tearDown(self):
        quiz._reset_quiz_bank_cache()
        state_module._meta_state.clear()
        state_module._meta_state.update(self._meta_state_snapshot)
        super().tearDown()

    async def test_external_wrong_result_records_correct_answer(self):
        prompt = (
            "【玄骨考校】\n"
            "@outerdao 你有 300 秒作答。\n"
            "玄骨上人问：“韩立在内殿对付玄骨时，最能克制魔修的手段是什么？”\n"
            "A. 青竹蜂云剑\n"
            "B. 辟邪神雷\n"
            "C. 乾蓝冰焰\n"
            "D. 啼魂兽\n"
            "回复本消息并使用 .作答 <选项>"
        )
        result = "【玄骨考校·答错】\n@outerdao 的答案 A 错了（正确答案：B）"

        with tempfile.TemporaryDirectory() as tmpdir:
            bank_path = Path(tmpdir) / "quiz_bank.json"
            bank_path.write_text("[]\n", encoding="utf-8")
            with (
                patch.object(quiz, "QUIZ_BANK_FILE", str(bank_path)),
                patch.object(quiz, "save_quiz_learning_watchers_state"),
                patch.object(quiz, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                self.assertTrue(await quiz.handle_quiz_learning_prompt(prompt, 1_700_000_000.0))
                self.assertIn("outerdao", state_module.get_quiz_learning_watchers())

                handled = await quiz.handle_quiz_result_broadcast(result, now=1_700_000_020.0)

            self.assertTrue(handled)
            self.assertEqual({}, state_module.get_quiz_learning_watchers())
            items = json.loads(bank_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(items))
            self.assertEqual("韩立在内殿对付玄骨时，最能克制魔修的手段是什么？", items[0]["question"])
            self.assertEqual("B", items[0]["answer"])
            self.assertIn("已记录新题", audit_mock.await_args.args[0])

    async def test_external_correct_result_records_answer(self):
        prompt = (
            "【玄骨考校】\n"
            "@outerdao 你有 300 秒作答。\n"
            "玄骨上人问：“按原著路线，乾蓝冰焰何时才适合开始从虚天鼎上缓慢炼出？”\n"
            "A. 结丹初期\n"
            "B. 结丹后期\n"
            "C. 元婴期\n"
            "D. 化神期\n"
            "回复本消息并使用 .作答 <选项>"
        )
        result = "【玄骨考校·答对】\n@outerdao 的答案 C 完全正确"

        with tempfile.TemporaryDirectory() as tmpdir:
            bank_path = Path(tmpdir) / "quiz_bank.json"
            bank_path.write_text("[]\n", encoding="utf-8")
            with (
                patch.object(quiz, "QUIZ_BANK_FILE", str(bank_path)),
                patch.object(quiz, "save_quiz_learning_watchers_state"),
                patch.object(quiz, "send_audit_log", new=AsyncMock()) as audit_mock,
            ):
                self.assertTrue(await quiz.handle_quiz_learning_prompt(prompt, 1_700_000_000.0))
                handled = await quiz.handle_quiz_result_broadcast(result, now=1_700_000_020.0)

            self.assertTrue(handled)
            items = json.loads(bank_path.read_text(encoding="utf-8"))
            self.assertEqual(1, len(items))
            self.assertEqual("C", items[0]["answer"])
            self.assertIn("已记录新题", audit_mock.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
