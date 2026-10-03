import asyncio
import copy
import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from model.audit_summary_health import HELD_GRACE_SEC, read_summary_health
from model.audit_summary_store import AuditSummaryStore, MAX_BYTES, validate_summary_checkpoint
from tools import health_observer


def checkpoint(*, held_at=None, rows=None, next_at=2000, retired=0):
    held = [] if held_at is None else [dict(id="batch", hash="a" * 64,
                                           message="secret message token=do-not-print", count=4, at=held_at)]
    return dict(version=1, rows=rows or [], held=held, next_at=next_at, retired_records=retired)


def row():
    return dict(bucket_key=["routine", "yuanying", 1, "a" * 64], identity_id=1,
                summary_kind="yuanying", count=3, seq=1, first_at=900, last_at=1000,
                first_ts="10:00", last_ts="10:01", html="<b>secret</b>", plain="secret")


def write_db(path, data):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS summary_checkpoint (id INTEGER PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT OR REPLACE INTO summary_checkpoint VALUES (1, ?)", (json.dumps(data),))


def test_absence_is_not_health_and_does_not_create_files(tmp_path):
    path = tmp_path / "unused" / "summary.db"
    optional = read_summary_health(path, 1000)
    assert optional["status"] == "not_created" and not optional["available"]
    assert not optional["alerts"]
    assert read_summary_health(path, 1000, required=True)["status"] == "error"
    assert not path.parent.exists()


@pytest.mark.parametrize("offset,expected", [(299.9, 0), (300, 1), (301, 1)])
def test_unconfirmed_grace_and_no_secret_leak(tmp_path, offset, expected):
    path = tmp_path / "summary.db"
    write_db(path, checkpoint(held_at=1000))
    before = path.read_bytes()
    result = read_summary_health(path, 1000 + offset)
    assert result["unresolved_batches"] == expected
    assert result["status"] == ("warn" if expected else "ok")
    assert "do-not-print" not in json.dumps(result)
    assert path.read_bytes() == before


def test_reads_committed_wal_and_keeps_pending_window(tmp_path):
    path = tmp_path / "summary #1.db"
    write_db(path, checkpoint())
    with sqlite3.connect(path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE summary_checkpoint SET payload=?", (json.dumps(checkpoint(rows=[row()])),))
        writer.commit()
        result = read_summary_health(path, 1500)
        assert result["pending_records"] == 3
        assert result["status"] == "ok"
        assert read_summary_health(path, 2900)["alerts"][0]["code"] == "notification_summary_overdue"


def test_retired_unknown_batches_do_not_become_healthy(tmp_path):
    path = tmp_path / "summary.db"
    write_db(path, checkpoint(retired=9))
    result = read_summary_health(path, 3000)
    assert result["status"] == "warn"
    assert result["alerts"][0]["code"] == "notification_summary_retired"


@pytest.mark.parametrize("bad", [None, {}, {"version": 2}, checkpoint(held_at=float("nan")),
                                  checkpoint(rows=[dict(row(), count=-1)]),
                                  checkpoint(rows=[row(), row()]), checkpoint(held_at=2000)])
def test_invalid_checkpoints_are_visible_and_unchanged(tmp_path, bad):
    path = tmp_path / "summary.db"
    write_db(path, bad)
    before = path.read_bytes()
    result = read_summary_health(path, 1000)
    assert result["status"] == "error"
    assert result["alerts"][0]["code"] == "notification_summary_unreadable"
    assert path.read_bytes() == before


def test_shared_validation_does_not_mutate_input():
    data = checkpoint(rows=[row()])
    original = copy.deepcopy(data)
    rows, *_ = validate_summary_checkpoint(data)
    assert isinstance(rows[0]["bucket_key"], tuple)
    assert data == original


def test_storage_write_failure_remains_visible_with_old_readable_checkpoint(tmp_path):
    path = tmp_path / "summary.db"
    write_db(path, checkpoint())
    assert read_summary_health(path, 1000)["status"] == "ok"
    assert health_observer.is_hard_journal_line(
        "audit summary checkpoint unavailable: CustomFailure; routine delivery held")


def test_schema_query_is_bounded(tmp_path):
    path = tmp_path / "summary.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE VIEW summary_checkpoint AS WITH RECURSIVE q(x) AS
                     (SELECT 1 UNION ALL SELECT x+1 FROM q WHERE x<100000000)
                     SELECT 1 AS id, cast(sum(x) AS TEXT) AS payload FROM q""")
    with patch("model.audit_summary_health.time.monotonic", side_effect=[1, 3]):
        assert read_summary_health(path, 1000)["status"] == "error"


def test_oversize_is_rejected_before_json_decode(tmp_path):
    path = tmp_path / "summary.db"
    write_db(path, "x" * (MAX_BYTES + 1))
    with patch("model.audit_summary_health.json.loads", side_effect=AssertionError("decoded oversized payload")):
        assert read_summary_health(path, 1000)["status"] == "error"


def test_missing_schema_and_symlink_never_repaired(tmp_path):
    path = tmp_path / "summary.db"
    with sqlite3.connect(path):
        pass
    assert read_summary_health(path, 1000)["status"] == "error"
    link = tmp_path / "link.db"
    link.symlink_to(path)
    assert read_summary_health(link, 1000)["status"] == "error"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_locked_database_reports_unknown_not_empty(tmp_path):
    path = tmp_path / "summary.db"
    write_db(path, checkpoint())
    with sqlite3.connect(path) as writer:
        writer.execute("BEGIN EXCLUSIVE")
        assert read_summary_health(path, 1000)["status"] == "error"


def test_monitor_accepts_real_store_unknown_receipt_without_replay(tmp_path):
    path = tmp_path / "summary.db"
    async def exercise():
        store = AuditSummaryStore(path, {}, [], clock=lambda: 1000)
        data = row()
        data["bucket_key"] = tuple(data["bucket_key"])
        store.bucket[data["bucket_key"]] = data
        store.order.append(data["bucket_key"])
        store.loaded = True
        sender = AsyncMock(return_value=False)
        assert not await store.flush(lambda _rows: "one message", sender, 1800)
        sender.assert_awaited_once()
        return store
    store = asyncio.run(exercise())
    assert read_summary_health(path, 1000 + HELD_GRACE_SEC)["unresolved_batches"] == 1
    assert len(store.held) == 1 and not store.bucket


def test_observer_includes_summary_risk_and_missing_flag(tmp_path):
    path = tmp_path / "data" / "state" / "audit_summary.db"
    path.parent.mkdir(parents=True)
    write_db(path, checkpoint(held_at=1))
    cfg = health_observer.build_config(health_observer.parse_args(["--project-root", str(tmp_path),
                                                                 "--notification-summary-required"]))
    with (
        patch.object(health_observer, "read_service_states", return_value={}),
        patch.object(health_observer, "read_safety_state", return_value={}),
        patch.object(health_observer, "read_listener_heartbeat", return_value={}),
        patch.object(health_observer, "read_foreign_xiuxian_processes", return_value=[]),
        patch.object(health_observer, "read_journal_matches", return_value={}),
        patch.object(health_observer, "collect_business_snapshot", return_value={}),
    ):
        snapshot = health_observer.collect_snapshot(cfg)
        assert snapshot["status"] == "warn"
        assert any(r["code"] == "notification_summary_unresolved" for r in snapshot["health"]["risk_reasons"])
        path.unlink()
        assert health_observer.collect_snapshot(cfg)["status"] == "error"


def test_import_does_not_load_game_runtime(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = """
import sys
def deny_network(event, args):
    if event in ('socket.__new__', 'socket.connect', 'socket.getaddrinfo'):
        raise AssertionError('network access')
sys.addaudithook(deny_network)
from model.audit_summary_health import read_summary_health
read_summary_health(sys.argv[1], 1000)
assert not any(name in sys.modules for name in ('model.runtime', 'model.state', 'model.config', 'model.persistence'))
"""
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path / "missing.db")],
                            cwd=root, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
