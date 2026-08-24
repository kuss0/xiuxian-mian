import json
import sqlite3

from tools.attempt_shadow_archive import build_report, export_archive, verify_archive
from tests.test_attempt_shadow_checkpoint import _prepare_db


def test_archive_requires_explicit_retention_boundary(tmp_path):
    db_path = tmp_path / "state.db"
    _prepare_db(db_path)

    report = build_report(db_path, now=2000.0)

    assert report["status"] == "report_only"
    assert report["selection"]["eligible"] is False
    assert report["counts"] == {"attempts": 0, "transitions": 0, "evidence": 0}


def test_archive_exports_complete_selected_timeline_and_manifest(tmp_path):
    db_path = tmp_path / "state.db"
    output_dir = tmp_path / "archives"
    _prepare_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE command_attempts SET intent_json = ?, business = ?",
        (json.dumps({"token": "must-not-leak", "safe": "value"}), "terminal_ok"),
    )
    conn.execute(
        "INSERT INTO command_attempt_transitions VALUES (?,?,?,?,?,?,?,?)",
        (1, "attempt:1", 1, "transport", "created", "sent", "sent", "archive-test"),
    )
    conn.commit()
    conn.close()

    report = build_report(db_path, before_ts=2000.0, now=3000.0)
    assert report["counts"] == {"attempts": 1, "transitions": 1, "evidence": 1}

    manifest_path, manifest = export_archive(
        db_path,
        output_dir,
        before_ts=2000.0,
        now=3000.0,
    )

    assert manifest["status"] == "verified"
    assert manifest["database_mutation"] == "none"
    assert manifest["deletion"] == "not_supported"
    assert manifest["counts"] == {"attempts": 1, "transitions": 1, "evidence": 1}
    assert manifest["files"]["attempts"]["min_ts"] == 1000.0
    assert manifest["files"]["attempts"]["max_ts"] == 1001.0
    assert manifest["files"]["evidence"]["min_ts"] == 1002.0
    assert manifest["files"]["evidence"]["max_ts"] == 1002.0
    assert verify_archive(manifest_path.parent) is True

    attempts = [
        json.loads(line)
        for line in (manifest_path.parent / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert attempts[0]["record"]["intent_json"]["token"] == "[REDACTED]"
    assert attempts[0]["record"]["intent_json"]["safe"] == "value"


def test_archive_excludes_transport_terminal_rows_with_open_business(tmp_path):
    db_path = tmp_path / "state.db"
    _prepare_db(db_path)

    report = build_report(db_path, before_ts=2000.0, now=3000.0)

    assert report["selection"]["business_states"] == [
        "terminal_ok",
        "terminal_fail",
        "abandoned",
    ]
    assert report["selection"]["op_ids"] == []
    assert report["counts"]["attempts"] == 0
