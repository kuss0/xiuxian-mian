import copy
import json
import sqlite3
from datetime import datetime

import pytest

from model.expedition_health import TZ, expedition_health, read_expedition_health


NOW = datetime(2026, 11, 15, 11, tzinfo=TZ).timestamp()
CONFIG = {"changfeng": {"enabled": True, "participant_identity_ids": [1, 2]},
          "zhengmo": {"enabled": True, "hour": 9, "identities": [{"identity_id": 1}]}}


def test_stale_held_occupancy_and_missed_daily_are_reported_without_mutation():
    records = {"weekly": {"runs": {"qipai": {"kind": "qipai", "phase": "held", "started_at": NOW - 86400,
                                                  "members": [{"identity_id": 1}, {"identity_id": 2}]}}},
               "changfeng": {"1": {"day": "2026-11-14", "phase": "done"}}}
    before = copy.deepcopy(records)
    alerts = expedition_health(CONFIG, records, {1}, NOW)
    assert [a["kind"] for a in alerts] == ["qipai", "changfeng", "zhengmo"]
    assert all(a["identity_ids"] == [1] for a in alerts)
    assert records == before


def test_normal_waiting_today_skipped_faction_and_future_timer_are_quiet():
    records = {"weekly": {"runs": {"qipai": {"kind": "qipai", "phase": "held", "started_at": NOW - 60,
                                                  "members": [{"identity_id": 1}]}}},
               "changfeng": {"1": {"day": "2026-11-15", "phase": "running"},
                             "2": {"day": "2026-11-14", "next_at": NOW + 60}},
               "zhengmo": {"1": {"day": "2026-11-15", "phase": "skipped"}}}
    assert not expedition_health(CONFIG, records, {1, 2}, NOW)
    assert not expedition_health(CONFIG, {}, {1, 2}, NOW - 7200)
    assert not expedition_health({}, {}, {1, 2}, NOW)


def _defeat_pause_record():
    return {
        "day": "2026-11-14", "phase": "done", "next_at": NOW - 3600,
        "auto_pause": {"reason": "confirmed_defeat", "run_id": "closed-native-run",
                       "arena": "longwind", "at": NOW - 86400, "requires_verified_win": True},
        "browser_trial": {"kind": "defeat", "run_id": "closed-native-run", "arena": "longwind",
                          "server_closed": True, "rewarded": True, "server_cleared": False},
    }


def test_held_and_confirmed_defeat_pause_explain_missing_daily_without_duplicate_alerts():
    records = {"changfeng": {
        "1": {"day": "2026-11-14", "phase": "held", "started_at": NOW - 1200,
              "browser_trial": {"kind": "not_started", "server_started": False,
                                "server_closed": False, "run_id": None}},
        "2": _defeat_pause_record(),
    }}
    before = copy.deepcopy(records)

    alerts = expedition_health({"changfeng": CONFIG["changfeng"]}, records, {1, 2}, NOW)

    assert len(alerts) == 2
    held, paused = alerts
    assert held["identity_ids"] == [1] and held["level"] == "at_risk"
    assert "持续占用" in held["reason"]
    assert paused["identity_ids"] == [2] and paused["level"] == "watch"
    assert paused["reason"] == "长风试炼：战败保护暂停，等待手动验证通关"
    assert all("今日尚无执行记录" not in alert["reason"] for alert in alerts)
    assert records == before


@pytest.mark.parametrize("started", [None, "invalid", 0, -1, True, False, float("nan"), float("inf"), NOW + 60])
@pytest.mark.parametrize("held_state", [{"phase": "held"}, {"phase": "interrupted"},
                                        {"phase": "running", "requires_reconciliation": True}])
def test_unknown_age_held_stays_at_risk_without_a_duplicate_missing_daily(started, held_state):
    run = {"day": "2026-11-14", **held_state}
    if started is not None:
        run["started_at"] = started
    records = {"changfeng": {"1": run}}
    before = json.dumps(records, sort_keys=True)

    alerts = expedition_health({"changfeng": CONFIG["changfeng"]}, records, {1}, NOW)

    assert len(alerts) == 1
    assert alerts[0]["level"] == "at_risk" and alerts[0]["identity_ids"] == [1]
    assert "开始时间缺失或无效" in alerts[0]["reason"]
    assert alerts[0]["age_sec"] is None
    assert "今日尚无执行记录" not in alerts[0]["reason"]
    assert json.dumps(records, sort_keys=True) == before


@pytest.mark.parametrize("age,expected", [(60, False), (899, False), (900, True), (1200, True)])
@pytest.mark.parametrize("field", ["started_at", "created_at"])
def test_known_held_age_preserves_fifteen_minute_grace_and_single_alert(age, expected, field):
    run = {"day": "2026-11-14", "phase": "held", field: NOW - age}
    alerts = expedition_health({"changfeng": CONFIG["changfeng"]}, {"changfeng": {"1": run}}, {1}, NOW)
    assert bool(alerts) is expected
    if expected:
        assert len(alerts) == 1 and alerts[0]["level"] == "at_risk"
        assert alerts[0]["age_sec"] == age and "超过15分钟" in alerts[0]["reason"]


@pytest.mark.parametrize("started", ["invalid", 0, False, NOW + 60])
def test_invalid_primary_time_is_not_silenced_by_recent_created_time_or_future_daily_timer(started):
    run = {"day": "2026-11-15", "phase": "held", "started_at": started,
           "created_at": NOW - 60, "next_at": NOW + 3600}
    alerts = expedition_health({"zhengmo": CONFIG["zhengmo"]}, {"zhengmo": {"1": run}}, {1}, NOW)
    assert len(alerts) == 1 and alerts[0]["level"] == "at_risk"
    assert "开始时间缺失或无效" in alerts[0]["reason"] and alerts[0]["age_sec"] is None


@pytest.mark.parametrize("taint", ["run_id", "open", "not_rewarded", "cleared", "unknown_pause", "invalid_time"])
def test_unverified_pause_stays_at_risk_with_its_own_reason(taint):
    run = _defeat_pause_record()
    if taint == "run_id":
        run["browser_trial"]["run_id"] = "another-run"
    elif taint == "open":
        run["browser_trial"]["server_closed"] = False
    elif taint == "not_rewarded":
        run["browser_trial"]["rewarded"] = False
    elif taint == "cleared":
        run["browser_trial"]["server_cleared"] = True
    elif taint == "unknown_pause":
        run["auto_pause"]["reason"] = "unknown"
    elif taint == "invalid_time":
        run["auto_pause"]["at"] = float("nan")

    alerts = expedition_health({"changfeng": CONFIG["changfeng"]},
                               {"changfeng": {"2": run}}, {2}, NOW)

    assert len(alerts) == 1
    assert alerts[0]["level"] == "at_risk"
    assert alerts[0]["reason"] == "长风试炼：自动暂停记录待核对，需检查暂停原因"


def test_timeout_with_confirmed_death_is_a_protective_pause():
    run = _defeat_pause_record()
    run["browser_trial"].update(kind="timeout", deaths=1)
    alerts = expedition_health({"changfeng": CONFIG["changfeng"]},
                               {"changfeng": {"2": run}}, {2}, NOW)
    assert len(alerts) == 1 and alerts[0]["level"] == "watch"


@pytest.mark.parametrize("state", ["today", "future"])
def test_existing_daily_or_future_wait_does_not_gain_an_extra_pause_alert(state):
    run = _defeat_pause_record()
    if state == "today":
        run["day"] = "2026-11-15"
    else:
        run["next_at"] = NOW + 3600
    assert expedition_health({"changfeng": CONFIG["changfeng"]},
                             {"changfeng": {"2": run}}, {2}, NOW) == []


def test_running_phase_only_warns_after_reply_deadline_and_grace():
    run = {"kind": "qipai", "phase": "running", "started_at": NOW - 1200,
           "members": [{"identity_id": 1}], "deadline": NOW + 60}
    records = {"weekly": {"runs": {"qipai": run}}}
    assert not expedition_health({}, records, {1}, NOW)
    run["deadline"] = NOW - 301
    assert expedition_health({}, records, {1}, NOW)[0]["kind"] == "qipai"


@pytest.mark.parametrize("suppression", [None, "paused", "recovery", "disabled", "frozen"])
def test_shared_database_reader_handles_suppression_and_old_schema(suppression):
    with sqlite3.connect(":memory:") as conn:
        assert read_expedition_health(conn, NOW) == []
        conn.executescript("CREATE TABLE meta(key TEXT,value TEXT); CREATE TABLE identities(send_as_id INTEGER,enabled INTEGER)")
        meta = {"expedition_config": CONFIG, "expedition_run_state": {}}
        if suppression == "paused":
            meta["global_enabled"] = False
        if suppression == "recovery":
            meta["global_recovery_hold_until"] = NOW + 60
        if suppression == "frozen":
            meta["channel_send_as_health"] = {"frozen_identity_ids": [1]}
        conn.executemany("INSERT INTO meta VALUES(?,?)", [(k, json.dumps(v)) for k, v in meta.items()])
        conn.execute("INSERT INTO identities VALUES(1,?)", (int(suppression != "disabled"),))
        rows = read_expedition_health(conn, NOW)
        assert len(rows) == (2 if suppression is None else 0)
        assert conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == len(meta)


