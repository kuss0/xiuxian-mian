import asyncio
import copy
from unittest.mock import AsyncMock, Mock

import pytest

from model import state as state_module
from model.features import quiz
from tools import health_observer


@pytest.fixture
def observation(monkeypatch):
    saved = copy.deepcopy(state_module._meta_state)
    state_module._meta_state.clear()
    state_module._meta_state.update(copy.deepcopy(state_module.GLOBAL_STATE_DEFAULTS))
    state_module.ensure_identity_registered(1001)
    state_module.update_send_as_profile(1001, username="fixturedao", enabled=True)
    audit, local, bank = AsyncMock(), Mock(), Mock(return_value=("added", {}))
    monkeypatch.setattr(quiz, "send_audit_log", audit)
    monkeypatch.setattr(quiz, "console_log", local)
    monkeypatch.setattr(quiz, "save_state", Mock())
    monkeypatch.setattr(quiz, "save_quiz_learning_watchers_state", Mock())
    monkeypatch.setattr(quiz, "_save_quiz_bank_entry", bank)
    try:
        yield audit, local, bank
    finally:
        state_module._meta_state.clear()
        state_module._meta_state.update(saved)


def watch(owned, bank_answer):
    state_module.set_quiz_learning_watchers({
        "fixturedao": {"target_tag": "@fixturedao", "identity_id": 1001 if owned else None,
                       "question": "fixture question", "options": {"A": "one", "B": "two"},
                       "expire_at": 1700000420.0, "matched_answer": bank_answer},
    })


def result_text(outcome):
    return ("【玄骨夺焰·答对】\n@fixturedao 的答案 A 完全正确！" if outcome == "correct"
            else "【玄骨夺焰·答错】\n@fixturedao 的答案 B 错了（正确答案：A）")


@pytest.mark.parametrize("outcome", ["correct", "wrong"])
@pytest.mark.parametrize("bank_state", ["known", "added", "exists"])
@pytest.mark.parametrize("owned", [False, True])
def test_routine_observation_preserves_learning_and_owned_notices(observation, monkeypatch, owned, bank_state, outcome):
    audit, local, bank = observation
    bank_answer = "A" if bank_state == "known" else ""
    watch(owned, bank_answer)
    bank.return_value = (bank_state, {})
    monkeypatch.setattr(quiz, "_match_quiz_answer", lambda *_: (bank_answer, "fixture"))
    assert asyncio.run(quiz.handle_quiz_result_broadcast(result_text(outcome), now=1700000050.0))
    assert not state_module.get_quiz_learning_watchers()
    if bank_state == "known":
        bank.assert_not_called()
    else:
        bank.assert_called_once_with("fixture question", {"A": "one", "B": "two", "C": "", "D": ""}, "A")
    if owned:
        audit.assert_awaited_once()
        assert audit.await_args.kwargs["send_as_id"] == 1001
        local.assert_not_called()
    else:
        audit.assert_not_awaited()
        local.assert_called_once()
        assert "外部题目结果｜未托管，仅学习观察｜记录：" in local.call_args.args[0]
        assert "fixture question" in local.call_args.args[0]
    assert not asyncio.run(quiz.handle_quiz_result_broadcast(result_text(outcome), now=1700000051.0))
    assert bank.call_count == (0 if bank_state == "known" else 1)
    assert audit.await_count == (1 if owned else 0)
    assert local.call_count == (0 if owned else 1)


@pytest.mark.parametrize("outcome", ["correct", "wrong"])
@pytest.mark.parametrize("bank_state", ["mismatch", "conflict", "write_failed"])
def test_shared_bank_problems_still_notify_for_external_players(observation, monkeypatch, bank_state, outcome):
    audit, local, bank = observation
    answer = "B" if bank_state == "mismatch" else ""
    watch(False, answer)
    monkeypatch.setattr(quiz, "_match_quiz_answer", lambda *_: (answer, "fixture"))
    bank.return_value = (bank_state, {"answer": "B"})
    assert asyncio.run(quiz.handle_quiz_result_broadcast(result_text(outcome), now=1700000050.0))
    audit.assert_awaited_once()
    local.assert_not_called()
    assert audit.await_args.kwargs["scope"] == "global"
    assert not state_module.get_quiz_learning_watchers()


@pytest.mark.parametrize("prefix", ["", "[2026-10-07 11:03:15] ",
                                      "Oct 07 11:03:15 host python[2901475]: [2026-10-07 11:03:15] "])
def test_passive_result_words_are_not_runtime_failures(prefix):
    line = prefix + "🦴 <code>@fixturedao</code>｜外部题目结果｜未托管，仅学习观察｜记录：ERROR FUSED 风暴 超时"
    assert not health_observer.is_hard_journal_line(line)
    assert not health_observer.is_warn_journal_line(line)


@pytest.mark.parametrize("line", [
    "ERROR writing observation: 🦴 <code>@fixturedao</code>｜外部题目结果｜未托管，仅学习观察｜记录：test",
    "🦴 <code>@fixturedao</code>｜外部题目结果｜记录：ERROR",
    "[wa] 题目：🦴 <code>@fixturedao</code>｜外部题目结果｜未托管，仅学习观察｜记录：ERROR",
    "ERROR 玄骨考校题库冲突，请人工处理",
])
def test_real_failures_are_not_hidden_by_passive_result_marker(line):
    assert health_observer.is_hard_journal_line(line)


@pytest.mark.parametrize("outcome", ["correct", "wrong"])
def test_actual_external_result_log_has_one_passive_line(observation, monkeypatch, outcome):
    audit, local, _ = observation
    watch(False, "")
    watchers = state_module.get_quiz_learning_watchers()
    watchers["fixturedao"]["question"] = "ERROR 风暴\n超时 FUSED"
    state_module.set_quiz_learning_watchers(watchers)
    monkeypatch.setattr(quiz, "_match_quiz_answer", lambda *_: ("", "fixture"))
    assert asyncio.run(quiz.handle_quiz_result_broadcast(result_text(outcome), now=1700000050.0))
    audit.assert_not_awaited()
    line = local.call_args.args[0]
    assert "\n" not in line
    assert "ERROR 风暴 超时 FUSED" in line
    assert not health_observer.is_hard_journal_line(line)
    assert not health_observer.is_warn_journal_line(line)
