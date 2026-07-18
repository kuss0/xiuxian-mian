import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from model import persistence_shadow
from tools import persistence_shadow_report


def _identity_snapshot(value):
    return ("profile", (value,), (), (), (), ())


def test_shadow_is_opt_in():
    with patch.dict(os.environ, {}, clear=True):
        assert persistence_shadow.is_enabled() is False


def test_shadow_counts_no_change_delta_and_candidate_backups():
    with tempfile.TemporaryDirectory() as tmp, patch.dict(
        os.environ,
        {
            "XIUXIAN_PERSISTENCE_SHADOW_ENABLED": "1",
            "XIUXIAN_PERSISTENCE_SHADOW_DIR": tmp,
            "XIUXIAN_PERSISTENCE_SHADOW_FLUSH_INTERVAL_SEC": "1",
            "XIUXIAN_LIVE_GUARD_BACKUP_INTERVAL_SEC": "60",
        },
    ):
        persistence_shadow.reset_for_tests()
        persistence_shadow.initialize(
            db_key="db",
            meta_snapshot={"global_enabled": "1"},
            identity_snapshots={1: _identity_snapshot(1), 2: _identity_snapshot(1)},
            initial_backup_at=100,
            now=100,
        )
        persistence_shadow.commit(
            persistence_shadow.capture(
                db_key="db",
                meta_snapshot={"global_enabled": "1"},
                identity_snapshots={1: _identity_snapshot(1), 2: _identity_snapshot(1)},
            ),
            now=101,
        )
        persistence_shadow.commit(
            persistence_shadow.capture(
                db_key="db",
                meta_snapshot={"global_enabled": "0"},
                identity_snapshots={1: _identity_snapshot(2), 2: _identity_snapshot(1)},
            ),
            now=161,
        )
        persistence_shadow.commit(
            persistence_shadow.capture(
                db_key="db",
                meta_snapshot={"global_enabled": "0"},
                identity_snapshots={1: _identity_snapshot(2)},
            ),
            now=162,
        )
        persistence_shadow.force_flush(now=163)
        rows = [
            json.loads(line)
            for path in Path(tmp).glob("*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()
        ]

    intervals = [row for row in rows if row["event"] == "interval"]
    assert sum(row["save_count"] for row in intervals) == 3
    assert sum(row["no_change_count"] for row in intervals) == 1
    assert sum(row["identity_changed_total"] for row in intervals) == 1
    assert sum(row["identity_deleted_total"] for row in intervals) == 1
    reasons = {}
    for row in intervals:
        for key, value in row["backup_reason_counts"].items():
            reasons[key] = reasons.get(key, 0) + value
    assert reasons == {"periodic": 1, "roster_changed": 1}
    serialized = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    assert "profile" not in serialized


def test_shadow_report_requires_time_and_restart_evidence():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "2026-07-18.jsonl"
        rows = [
            {"event": "baseline", "ts_epoch": 100, "pid": 1, "process_started_at": 90},
            {
                "event": "interval",
                "ts_epoch": 100 + 13 * 3600,
                "started_at": 100,
                "ended_at": 100 + 13 * 3600,
                "pid": 2,
                "process_started_at": 200,
                "save_count": 10,
                "no_change_count": 7,
                "changed_save_count": 3,
                "identity_changed_total": 3,
                "identity_changed_max": 1,
                "meta_changed_total": 1,
                "meta_changed_max": 1,
                "full_scope_count": 0,
                "identity_deleted_total": 0,
                "telemetry_error_count": 0,
                "backup_reason_counts": {"periodic": 2},
                "meta_key_counts": {"global_enabled": 1},
            },
        ]
        path.write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )
        result = persistence_shadow_report.build_report(
            Path(tmp),
            since_hours=24,
            now=100 + 13 * 3600,
        )

    assert result["observation_hours"] == 13
    assert result["process_sessions"] == 2
    assert result["p1_review_ready"] is True
    assert result["p2_review_ready"] is False


def test_shadow_safe_helpers_never_raise_on_bad_output_path():
    with patch.dict(
        os.environ,
        {
            "XIUXIAN_PERSISTENCE_SHADOW_ENABLED": "1",
            "XIUXIAN_PERSISTENCE_SHADOW_DIR": "/dev/null/not-a-directory",
        },
    ):
        persistence_shadow.reset_for_tests()
        persistence_shadow.safe_initialize(
            db_key="db",
            meta_snapshot={},
            identity_snapshots={},
            initial_backup_at=0,
            now=100,
        )
        sample = persistence_shadow.safe_capture(
            db_key="db",
            meta_snapshot={},
            identity_snapshots={},
        )
        assert sample is not None
        persistence_shadow.safe_commit(sample, now=200)
