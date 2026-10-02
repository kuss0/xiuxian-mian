from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from tools import native_fishing_report as report
from test_fishing_dwelling_journal import OWNER, Store, response, start
from test_fishing_dwelling_supply import Store as SupplyStore, prepare, shop_context


DAY = "2026-10-02"
NOW = datetime(2026, 10, 2, 11, tzinfo=report.TZ).timestamp()
CONFIG = {"cave_public_fishing_identity_ids": [OWNER[0]], "cave_public_fishing_enabled": True,
          "cave_public_entry_url": "https://example.invalid/?secret=DO_NOT_PRINT"}
ACCOUNTS = {str(OWNER[0]): OWNER[1]}


def row(**changes):
    return {"send_as_id": OWNER[0], "label": "fixture", "username": "", "enabled": 0,
            "fishing_enabled": 0, "next_fishing_time": NOW + 3600,
            "fishing_native_operation": "{}", "fishing_native_supply": "{}",
            "fishing_operation": "{}", "fishing_result_pending": "{}",
            "fishing_daily_day": DAY, "fishing_daily_count": 0, "fishing_daily_limit": 5,
            "fishing_daily_catch_summary_json": "", "fishing_last_result": "",
            "concubine_voyage_return_at": 0, "concubine_voyage_status": "", **changes}


def summarize(item, *, config=CONFIG, accounts=ACCOUNTS):
    return report.summarize_identity(item, config=config, accounts=accounts, now=NOW)


def test_selected_frozen_channel_is_not_claimed_as_a_completed_run():
    result = summarize(row())
    assert result["selected"] and not result["group_identity_enabled"]
    assert result["schedule"] == "scheduled" and result["evidence"] == "none"
    assert result["summary"] is None and not result["warnings"]


@pytest.mark.parametrize("message,expected", list(report.SKIPS.items()))
def test_skip_evidence_is_not_a_settlement(message, expected):
    result = summarize(row(fishing_last_result=message))
    assert result["evidence"] == expected and result["summary"] is None
    assert summarize(row(fishing_last_result=message, fishing_daily_day="2026-10-01"))["evidence"] == "none"


@pytest.mark.parametrize("value", ["{bad", "null", "[]", '{"phase":"accounted","token":"DO_NOT_PRINT"}'])
def test_invalid_receipt_cannot_hide_behind_disabled_flag(value):
    result = summarize(row(fishing_native_operation=value), config={})
    assert not result["selected"]
    assert "fishing_native_operation:invalid" in result["warnings"]
    assert "DO_NOT_PRINT" not in json.dumps(result)


@pytest.mark.parametrize("phase", ["cast_pending", "session_owned", "settled", "accounted"])
def test_receipts_use_real_validator_without_assuming_daily_completion(monkeypatch, phase):
    monkeypatch.setattr(report.cast_journal.time, "time", lambda: NOW)
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    if phase != "cast_pending":
        ledger.accept(query, response(settled=phase in ("settled", "accounted")))
    if phase == "accounted":
        store.record["phase"] = phase
        store.record["revision"] += 1
    result = summarize(row(fishing_native_operation=json.dumps(store.record)))
    assert result["fishing_native_operation"] == phase
    assert bool(result["warnings"]) == (phase != "accounted")
    assert result["evidence"] == "none"
    result = summarize(row(fishing_native_operation=store.record), accounts={str(OWNER[0]): 2})
    assert "fishing_native_operation:owner_mismatch" in result["warnings"]


@pytest.mark.parametrize("key", ["fishing_native_supply", "fishing_operation", "fishing_result_pending"])
def test_other_unresolved_operations_remain_visible(key):
    result = summarize(row(**{key: '{"pending":true}'}))
    assert any(warning.startswith(key + ":") for warning in result["warnings"])


@pytest.mark.parametrize("phase", ["pending", "confirmed", "accounted"])
def test_supply_receipt_is_validated_independently_of_cast(phase):
    store = SupplyStore()
    ledger = store.open(owner=OWNER)
    _, expected, _ = prepare(ledger)
    if phase != "pending":
        ledger.accept(expected, shop_context(rice=20, stone=4300))
    if phase == "accounted":
        store.record["phase"] = phase
        store.record["revision"] += 1
    result = summarize(row(fishing_native_supply=json.dumps(store.record)))
    assert result["fishing_native_supply"] == phase
    assert bool(result["warnings"]) == (phase != "accounted")
    assert result["evidence"] == "none"


def test_summary_does_not_double_count_a_receipt_or_replace_server_quota():
    saved = {"day": DAY, "rods": 6, "fish": {"fish": 1}, "rewards": {"weed": 2}}
    result = summarize(row(fishing_daily_count=5, fishing_daily_catch_summary_json=json.dumps(saved)))
    assert result["summary"] == {key: value for key, value in saved.items() if key != "day"}
    assert result["quota"] == {"used": 5, "limit": 5}
    assert "summary_rods_exceed_quota_used" in result["warnings"]
    assert result["evidence"] == "recorded_settlements"


def test_prior_day_summary_is_not_todays_catch():
    saved = {"day": "2026-10-01", "rods": 5, "fish": {"old": 5}, "rewards": {}}
    result = summarize(row(fishing_daily_catch_summary_json=json.dumps(saved)))
    assert result["summary"] is None and result["evidence"] == "none"


@pytest.mark.parametrize("bad", [-1, True, "3", float("nan")])
def test_bad_count_is_not_coerced_into_a_gain(bad):
    summary = {"day": DAY, "rods": 1, "fish": {"fish": bad}, "rewards": {}}
    result = summarize(row(fishing_daily_catch_summary_json=json.dumps(summary)))
    assert "daily_summary:invalid" in result["warnings"] and result["summary"] is None


@pytest.mark.parametrize("bad", [None, -1, True, float("inf"), float("nan"), "123"])
def test_malformed_clock_cannot_be_reported_as_scheduled(bad):
    result = summarize(row(next_fishing_time=bad))
    assert result["schedule"] == "invalid"


def test_configuration_does_not_treat_string_zero_as_enabled():
    with pytest.raises(ValueError, match="flag"):
        summarize(row(), config={**CONFIG, "cave_public_fishing_enabled": "0"})
    assert not summarize(row(), config={**CONFIG, "cave_public_entry_url": " "})["selected"]
    assert summarize(row(fishing_enabled=1), config={})["selected"]


def test_report_does_not_mutate_input_or_expose_config():
    original = row(concubine_voyage_status="sailing", concubine_voyage_return_at=NOW + 7200)
    before = deepcopy(original)
    result = summarize(original)
    assert original == before and result["voyage_return_time"]
    assert "DO_NOT_PRINT" not in json.dumps(result)


def create_db(path):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT)")
    conn.executemany("INSERT INTO meta VALUES (?,?)", [
        ("miniapp_auto_config", json.dumps(CONFIG)), ("identity_account_map", json.dumps(ACCOUNTS))])
    conn.execute("CREATE TABLE identities (send_as_id INTEGER,username TEXT,label TEXT,enabled INTEGER)")
    conn.execute("INSERT INTO identities VALUES (?,?,?,?)", (OWNER[0], "fixture", "", 0))
    conn.execute("CREATE TABLE identity_module_state (send_as_id INTEGER,fishing_enabled INTEGER)")
    conn.execute("INSERT INTO identity_module_state VALUES (?,0)", (OWNER[0],))
    conn.execute("CREATE TABLE identity_timers (send_as_id INTEGER,next_fishing_time REAL)")
    conn.execute("INSERT INTO identity_timers VALUES (?,?)", (OWNER[0], NOW + 3600))
    columns = ",".join(report.RUNTIME_FIELDS)
    conn.execute("CREATE TABLE identity_runtime_state (send_as_id INTEGER," + columns + ")")
    values = [row().get(key) for key in report.RUNTIME_FIELDS]
    conn.execute("INSERT INTO identity_runtime_state VALUES (" + ",".join("?" for _ in [OWNER[0], *values]) + ")",
                 [OWNER[0], *values])
    conn.commit()
    return conn


def test_read_snapshot_reads_committed_wal_and_leaves_db_unchanged(tmp_path):
    path = tmp_path / "state with space.db"
    conn = create_db(path)
    try:
        before = list(conn.iterdump())
        assert Path(str(path) + "-wal").stat().st_size > 0
        result = report.build_report(path, now=NOW)
        assert result["selected"] == 1 and result["read_only"] is True
        assert result["evidence_counts"] == {"none": 1}
        assert list(conn.iterdump()) == before
        assert "DO_NOT_PRINT" not in json.dumps(result)
    finally:
        conn.close()


def test_missing_db_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        report.build_report(path, now=NOW)
    assert not path.exists()


def test_cli_is_offline_and_does_not_import_runtime_state(tmp_path):
    path = tmp_path / "isolated.db"
    conn = create_db(path)
    conn.close()
    script = """
import socket, sys
def no_network(*args, **kwargs):
    raise AssertionError('network access')
socket.socket = no_network
from tools import native_fishing_report
sys.argv = ['report', '--db', sys.argv[1], '--now', sys.argv[2]]
assert native_fishing_report.main() == 0
assert 'model.state' not in sys.modules
assert 'model.persistence' not in sys.modules
"""
    process = subprocess.run([sys.executable, "-c", script, str(path), str(NOW)], cwd=report.ROOT,
                             capture_output=True, text=True, timeout=15)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout)["selected"] == 1


def test_cli_missing_database_returns_failure_without_raw_path(tmp_path):
    path = tmp_path / "DO_NOT_PRINT.db"
    process = subprocess.run([sys.executable, str(report.ROOT / "tools/native_fishing_report.py"),
                              "--db", str(path)], capture_output=True, text=True, timeout=15)
    assert process.returncode == 1 and not path.exists()
    assert json.loads(process.stdout)["error"] == "snapshot_unavailable_or_invalid"
    assert "DO_NOT_PRINT" not in process.stdout + process.stderr
