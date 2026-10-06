import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from tools import defensive_preflight as preflight
from test_defensive_preflight import _create_preflight_db


TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 7, 6, 20, tzinfo=TZ).timestamp()
TOMORROW = datetime(2026, 10, 8, 1, 38, 41, tzinfo=TZ).timestamp()


def closed_row(**changes):
    return {
        "send_as_id": 7538826434, "label": "Lsfnqy", "username": "Lpprceqei",
        "duel_enabled": 1, "duel_daily_completed_day": "2026-10-07",
        "duel_completed_count": 0, "duel_reply_to_msg_id": 0,
        "duel_open_msg_id": 0, "duel_last_result": "completed",
        "next_duel_time": TOMORROW, **changes,
    }


@pytest.mark.parametrize("changes,code", [
    ({"duel_completed_count": 1}, "progress_reopened"),
    ({"next_duel_time": NOW + 600}, "schedule_reopened"),
    ({"next_duel_time": 0}, "schedule_reopened"),
    ({"duel_reply_to_msg_id": 100}, "pending_after_close"),
    ({"duel_open_msg_id": 101}, "pending_after_close"),
])
def test_closed_day_inconsistency_is_observable(changes, code):
    item = preflight._duel_daily_status(closed_row(**changes), NOW)
    assert item["level"] == "at_risk"
    assert code in item["anomalies"]


@pytest.mark.parametrize("phase", ["restore_needed", "restore_unequip_wait", "restore_equip:0", "restore_equip_wait:0"])
def test_active_restoration_is_not_batch_reopening(phase):
    item = preflight._duel_daily_status(closed_row(
        duel_last_result=f"斗法配装:{phase}", duel_completed_count=10, next_duel_time=NOW + 5,
    ), NOW)
    assert item["level"] == "watch"
    assert item["anomalies"] == []


def test_restored_state_is_not_exempt_from_closure_check():
    item = preflight._duel_daily_status(closed_row(
        duel_last_result="斗法配装:restored", duel_completed_count=1,
    ), NOW)
    assert item["anomalies"] == ["progress_reopened"]


def test_normal_closure_and_day_boundary():
    assert preflight._duel_daily_status(closed_row(), NOW)["level"] == "healthy"
    assert preflight._duel_daily_status(closed_row(), TOMORROW) is None
    assert preflight._duel_daily_status(closed_row(duel_enabled=0), NOW) is None
    assert preflight._duel_daily_status(closed_row(duel_daily_completed_day=""), NOW) is None


def test_restoration_does_not_hide_another_pending_duel():
    item = preflight._duel_daily_status(closed_row(
        duel_last_result="斗法配装:restore_equip_wait:0", duel_completed_count=10,
        next_duel_time=NOW, duel_reply_to_msg_id=100,
    ), NOW)
    assert item["anomalies"] == ["pending_after_close"]
    assert item["level"] == "at_risk"


def test_game_midnight_not_host_day_controls_closure():
    before_midnight = datetime(2026, 10, 7, 23, 59, 59, tzinfo=TZ).timestamp()
    assert preflight._duel_daily_status(closed_row(), before_midnight)["level"] == "healthy"
    assert preflight._duel_daily_status(closed_row(), before_midnight + 1) is None


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with patch.object(preflight, "DB_PATH", path), patch.object(preflight, "_listener_status", return_value={}), \
            pytest.raises(sqlite3.OperationalError):
        preflight.snapshot(horizon_sec=3600)
    assert not path.exists()


@pytest.mark.parametrize("identity_enabled", [True, False])
def test_snapshot_observes_closure_without_writing(tmp_path, identity_enabled):
    path = tmp_path / "preflight.db"
    _create_preflight_db(path, identity_enabled=identity_enabled)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE identity_module_state ADD COLUMN duel_enabled INTEGER DEFAULT 1")
        conn.execute("ALTER TABLE identity_timers ADD COLUMN next_duel_time REAL DEFAULT 0")
        for name, definition in (
            ("duel_daily_completed_day", "TEXT DEFAULT '2026-10-07'"),
            ("duel_completed_count", "INTEGER DEFAULT 1"),
            ("duel_reply_to_msg_id", "INTEGER DEFAULT 0"),
            ("duel_open_msg_id", "INTEGER DEFAULT 0"),
            ("duel_last_result", "TEXT DEFAULT ''"),
        ):
            conn.execute(f"ALTER TABLE identity_runtime_state ADD COLUMN {name} {definition}")
    before = path.read_bytes()
    with patch.object(preflight, "DB_PATH", path), patch.object(preflight.time, "time", return_value=NOW), \
            patch.object(preflight, "_listener_status", return_value={"level": "healthy", "module": "listener"}), \
            patch.object(preflight, "_recent_script_sends", return_value=[]):
        data = preflight.snapshot(horizon_sec=3600)
    items = [item for item in data["checks"] if item["module"] == "duel"]
    assert len(items) == int(identity_enabled)
    if items:
        assert items[0]["level"] == "at_risk"
        assert data["status"] == "at_risk"
    assert path.read_bytes() == before


def test_old_schema_remains_readable(tmp_path):
    path = tmp_path / "old.db"
    _create_preflight_db(path, identity_enabled=False)
    with patch.object(preflight, "DB_PATH", path), patch.object(preflight.time, "time", return_value=NOW), \
            patch.object(preflight, "_listener_status", return_value={"level": "healthy", "module": "listener"}):
        data = preflight.snapshot(horizon_sec=3600)
    assert not any(item["module"] == "duel" for item in data["checks"])
