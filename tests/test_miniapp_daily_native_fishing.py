from datetime import datetime
import json
import sqlite3
import subprocess
import sys
from unittest.mock import Mock

import pytest

from tools import miniapp_daily_report as daily
from tools import native_fishing_report as native


DAY = "2026-10-09"


def snapshot(**changes):
    row = dict(identity_id=123, evidence="recorded_settlements", warnings=[],
               fishing_native_operation="accounted", latest_native_cast_day=DAY,
               summary={"rods": 3, "fish": {"fish": 2}, "rewards": {"weed": 1}},
               selected=False)
    row.update(changes)
    return {"read_only": True, "identities": [row]}


def test_native_summary_replaces_old_capture_without_double_counting(tmp_path, monkeypatch):
    reader = Mock(return_value=snapshot())
    monkeypatch.setattr(native, "build_report", reader)
    legacy = Mock(side_effect=AssertionError("native and legacy must not be summed"))
    monkeypatch.setattr(daily, "summarize_fishing", legacy)
    output = daily.build_report(DAY, tmp_path, fishing_db=tmp_path / "state.db")
    assert "3竿" in output and "fishx2" in output and "weedx1" in output
    assert "暂无" not in output and "空竿" not in output
    assert "未启用" not in output
    reader.assert_called_once()
    assert datetime.fromtimestamp(reader.call_args.kwargs["now"], daily.TZ_LOCAL).strftime("%Y-%m-%d") == DAY


@pytest.mark.parametrize("changes", [
    {"warnings": ["daily_summary:invalid"]},
    {"warnings": ["summary_rods_exceed_quota_used"]},
    {"warnings": ["fishing_native_operation:owner_mismatch"]},
    {"fishing_native_operation": "none"},
    {"latest_native_cast_day": "2026-10-08"},
    {"evidence": "no_rod", "summary": None},
    {"evidence": "none", "summary": None},
])
def test_unconfirmed_native_evidence_is_not_a_gain(tmp_path, monkeypatch, changes):
    monkeypatch.setattr(native, "build_report", lambda *args, **kwargs: snapshot(**changes))
    output = daily.build_report(DAY, tmp_path, fishing_db=tmp_path / "state.db")
    assert "fishx" not in output and "weedx" not in output
    assert "不代表未执行" in output


@pytest.mark.parametrize("error", [sqlite3.OperationalError("private detail"), ValueError("secret")])
def test_missing_or_invalid_database_reports_unknown_not_zero(tmp_path, monkeypatch, error):
    monkeypatch.setattr(native, "build_report", Mock(side_effect=error))
    output = daily.build_report(DAY, tmp_path, fishing_db=tmp_path / "missing.db")
    assert "不可用" in output and "private" not in output and "secret" not in output
    assert "暂无 MiniApp 结算成果" not in output
    assert not (tmp_path / "missing.db").exists()


def test_cli_custom_capture_stays_offline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["report", "--day", DAY, "--capture-dir", str(tmp_path)])
    monkeypatch.setattr(native, "build_report", Mock(side_effect=AssertionError("live read forbidden")))
    daily.main()
    assert "原生钓鱼账本未纳入" in capsys.readouterr().out


def test_cli_defaults_to_readonly_native_source(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["report", "--day", DAY])
    monkeypatch.setattr(daily, "CAPTURE_DIR", tmp_path)
    monkeypatch.setattr(daily, "STATE_DIR", tmp_path)
    monkeypatch.delenv("XIUXIAN_DB_FILE", raising=False)
    reader = Mock(return_value=snapshot())
    monkeypatch.setattr(native, "build_report", reader)
    daily.main()
    assert reader.call_args.args[0] == tmp_path / "chaogu_state.db"
    assert "fishx2" in capsys.readouterr().out


def test_real_readonly_snapshot_uses_requested_day_and_keeps_database(tmp_path):
    from tests.test_native_fishing_report import create_db, DAY as SAVED_DAY

    path = tmp_path / "state.db"
    conn = create_db(path)
    conn.execute("UPDATE identity_runtime_state SET fishing_daily_count=3, "
                 "fishing_daily_catch_summary_json=?", (json.dumps({
                     "day": SAVED_DAY, "rods": 3, "fish": {"saved": 2}, "rewards": {},
                 }),))
    conn.commit()
    before = list(conn.iterdump())
    output = daily.build_report(DAY, tmp_path, fishing_db=path)
    assert "savedx" not in output and "不代表未执行" in output
    assert list(conn.iterdump()) == before
    conn.close()


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    output = daily.build_report(DAY, tmp_path, fishing_db=path)
    assert "不可用" in output and not path.exists()


def test_real_native_settlement_is_reported_once_without_network_or_writes(tmp_path, monkeypatch):
    from tests.test_native_fishing_report import create_db, DAY as SAVED_DAY, NOW
    from tests.test_fishing_dwelling_journal import Store, response, start

    monkeypatch.setattr(native.cast_journal.time, "time", lambda: NOW)
    store = Store()
    ledger = store.open()
    query, _ = start(ledger)
    ledger.accept(query, response(settled=True))
    store.record.update(phase="accounted", revision=store.record["revision"] + 1)
    path = tmp_path / "state.db"
    conn = create_db(path)
    try:
        conn.execute("UPDATE identity_runtime_state SET fishing_daily_count=3, "
                     "fishing_daily_catch_summary_json=?,fishing_native_operation=?", (
                         json.dumps({"day": SAVED_DAY, "rods": 3, "fish": {"saved": 2}, "rewards": {}}),
                         json.dumps(store.record),
                     ))
        conn.commit()
        before = list(conn.iterdump())
        script = """
import socket, ssl, sys
def forbidden(*args, **kwargs):
    raise AssertionError('network access')
socket.socket = forbidden
from tools import miniapp_daily_report
sys.argv = ['report', '--day', sys.argv[1], '--capture-dir', sys.argv[2], '--fishing-db', sys.argv[3]]
miniapp_daily_report.main()
assert 'model.state' not in sys.modules
assert 'model.persistence' not in sys.modules
"""
        result = subprocess.run([sys.executable, "-c", script, SAVED_DAY, str(tmp_path), str(path)],
                                cwd=daily.PROJECT_ROOT, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        assert result.stdout.count("savedx2") == 1 and "3竿" in result.stdout
        assert list(conn.iterdump()) == before
    finally:
        conn.close()
