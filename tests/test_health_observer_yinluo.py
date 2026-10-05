import copy
import json
import sqlite3
from datetime import datetime

import pytest

from tools import health_observer as observer


@pytest.fixture
def snapshot():
    now = datetime(2026, 10, 6, 3, 0).timestamp()
    return now, {
        "yinluo_enabled": True,
        "yinluo_observation": {
            "auto_config": {"daily_sacrifice": True, "soothe": True},
            "last_daily_sacrifice_day": "2026-09-18",
            "next_daily_sacrifice_time": now - 18 * 86400,
            "last_observed_at": now - 60,
            "banner_name": "test banner",
            "exhausted_slot_numbers": [1],
            "auto_last_action": "soothe",
            "auto_last_error": "修为账本待校准，不发送安抚幡灵。",
            "auto_next_time": now + 3600,
        },
        "yinluo_accounting": {"hold": "", "book": {"gap": None}, "operations": []},
    }


def test_starvation_is_visible_despite_future_auto_wait_without_mutation(snapshot):
    now, state = snapshot
    before = copy.deepcopy(state)
    result = observer.yinluo_daily_health(state.get, now)
    assert result["reason"] == "soothe_starves_daily"
    assert result["overdue_sec"] == 10740
    assert state == before


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("last_daily_sacrifice_day", "2026-10-06", "completed"),
    ("last_daily_sacrifice_day", "2026-10-07", "evidence_missing"),
    ("last_daily_sacrifice_day", "broken", "evidence_missing"),
    ("last_observed_at", 0, "evidence_missing"),
    ("last_observed_at", 1, "evidence_missing"),
    ("last_observed_at", float("inf"), "evidence_missing"),
    ("next_daily_sacrifice_time", "invalid", "evidence_missing"),
    ("next_daily_sacrifice_time", float("nan"), "evidence_missing"),
    ("next_daily_sacrifice_time", 9_999_999_999, "cooldown"),
    ("auto_config", {"daily_sacrifice": False}, "disabled"),
    ("auto_config", {"daily_sacrifice": True, "soothe": False}, "due"),
    ("auto_last_action", "resource_hold", "resource_hold"),
    ("auto_last_action", "resource_capacity", "resource_hold"),
    ("auto_last_action", "legacy_pending", "resource_hold"),
    ("auto_last_action", "resource_pending", "pending"),
    ("auto_last_action", "daily_sacrifice", "due"),
    ("last_result", "pending", "pending"),
    ("last_result", "not_member", "evidence_missing"),
    ("auto_soothe_pending", {"slot": 1}, "pending"),
    ("auto_collect_pending", {"slots": [1]}, "pending"),
    ("auto_refine_pending", {"slot": 1}, "pending"),
    ("auto_calibrate_reason", "await banner", "evidence_missing"),
    ("auto_last_error", "当前修为 11，不足以安抚幡灵 50 点。", "soothe_starves_daily"),
    ("auto_last_error", "waiting for something else", "due"),
    ("banner_name", "", "evidence_missing"),
])
def test_observation_boundaries(snapshot, field, value, reason):
    now, state = snapshot
    state["yinluo_observation"][field] = value
    assert observer.yinluo_daily_health(state.get, now)["reason"] == reason


@pytest.mark.parametrize("phase", ["prepared", "sent", "unknown"])
def test_resource_operation_is_not_starvation(snapshot, phase):
    now, state = snapshot
    state["yinluo_accounting"]["operations"] = [{"phase": phase}]
    assert observer.yinluo_daily_health(state.get, now)["reason"] == "pending"


@pytest.mark.parametrize(("accounting", "reason"), [
    ({}, "evidence_missing"),
    ({"book": {}, "operations": [None]}, "evidence_missing"),
    ({"book": {}, "operations": [{"phase": []}]}, "evidence_missing"),
    ({"book": {}, "operations": [{"phase": {}}]}, "evidence_missing"),
    ({"book": {}, "operations": [{"phase": "bogus"}]}, "evidence_missing"),
    ({"book": {}, "operations": [], "hold": "legacy_pending"}, "resource_hold"),
    ({"book": {"gap": {"reason": "source mismatch"}}, "operations": []}, "resource_hold"),
    ({"book": {}, "operations": [{"phase": "complete"}]}, "soothe_starves_daily"),
])
def test_accounting_boundaries(snapshot, accounting, reason):
    now, state = snapshot
    state["yinluo_accounting"] = accounting
    assert observer.yinluo_daily_health(state.get, now)["reason"] == reason


@pytest.mark.parametrize(("minutes", "reason"), [(0, "due"), (10, "due"), (11, "due"), (12, "soothe_starves_daily")])
def test_midnight_grace_does_not_use_old_due_timestamp(snapshot, minutes, reason):
    _, state = snapshot
    now = datetime(2026, 10, 6, 0, minutes).timestamp()
    state["yinluo_observation"]["last_observed_at"] = now - 10
    assert observer.yinluo_daily_health(state.get, now)["reason"] == reason


@pytest.mark.parametrize("key", ["deep_retreat", "yuanying"])
@pytest.mark.parametrize(("phase", "offset", "reason"), [
    ("running", 600, "soothe_starves_daily"),
    ("running", 60, "scheduling_blocked"),
    ("running", -60, "scheduling_blocked"),
    ("summary_due", 600, "scheduling_blocked"),
    ("launching", 600, "scheduling_blocked"),
])
def test_phaseful_wait_only_suppresses_at_actual_risk_window(snapshot, key, phase, offset, reason):
    now, state = snapshot
    state.update({f"{key}_enabled": True, f"{key}_phase": phase, f"next_{key}_time": now + offset})
    assert observer.yinluo_daily_health(state.get, now)["reason"] == reason


@pytest.mark.parametrize(("enabled", "paused", "blocked", "expected"), [
    (True, False, (), "warn"),
    (True, True, (), "active"),
    (True, False, (1,), "active"),
    (False, False, (), None),
])
def test_module_summary_integration_is_read_only(snapshot, enabled, paused, blocked, expected):
    now, state = snapshot
    with sqlite3.connect(":memory:") as conn:
        conn.row_factory = sqlite3.Row
        conn.executescript("""
            CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, enabled INTEGER);
            CREATE TABLE identity_module_state(send_as_id INTEGER PRIMARY KEY, yinluo_enabled INTEGER);
            CREATE TABLE identity_runtime_state(send_as_id INTEGER PRIMARY KEY, yinluo_observation TEXT, yinluo_accounting TEXT);
            INSERT INTO identities VALUES (1, 1);
        """)
        conn.execute("INSERT INTO identity_module_state VALUES (1, ?)", (enabled,))
        conn.execute("INSERT INTO identity_runtime_state VALUES (1, ?, ?)", (
            json.dumps(state["yinluo_observation"]), json.dumps(state["yinluo_accounting"]),
        ))
        conn.execute("PRAGMA query_only=ON")
        changes = conn.total_changes
        result = observer.build_module_summary(conn, now, global_paused=paused, command_blocked_ids=blocked)
        assert conn.total_changes == changes
        if expected is None:
            assert result == []
        else:
            assert len(result) == 1
            assert result[0]["module"] == "yinluo"
            assert result[0]["status"] == expected
            assert "yinluo_daily" in result[0]["evidence"]
