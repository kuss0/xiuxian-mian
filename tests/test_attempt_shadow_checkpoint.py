import json
import sqlite3
from pathlib import Path

from tools.attempt_shadow_checkpoint import build_checkpoint


def _prepare_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE command_attempts (
            op_id TEXT PRIMARY KEY, chain_id TEXT, send_as_id INTEGER, account_id INTEGER,
            source_module TEXT, command TEXT, command_family TEXT, priority TEXT,
            intent_json TEXT, transport TEXT, business TEXT, recovery_policy TEXT,
            block_code TEXT, block_reason TEXT, definitely_unsent INTEGER,
            root_msg_id INTEGER, reply_to_msg_id INTEGER, result_msg_id INTEGER,
            resend_count INTEGER, max_resend INTEGER, transport_due_at REAL,
            business_due_at REAL, business_code TEXT, business_summary TEXT,
            last_error TEXT, last_transition_key TEXT, meta_json TEXT, version INTEGER,
            created_at REAL, updated_at REAL, sent_at REAL, closed_at REAL
        );
        CREATE TABLE command_attempt_transitions (
            id INTEGER PRIMARY KEY, op_id TEXT, seq INTEGER, axis TEXT,
            from_state TEXT, to_state TEXT, transition_key TEXT, code TEXT
        );
        CREATE TABLE command_attempt_evidence (
            id INTEGER PRIMARY KEY, op_id TEXT, seq INTEGER, kind TEXT, msg_id INTEGER,
            edit_seq INTEGER, family TEXT, text_digest TEXT, source TEXT,
            idempotency_key TEXT, ts REAL, payload_json TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO command_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "attempt:1", "", 1, 1, "test", ".测试", "test", "normal", "{}",
            "sent", "open", "wait_late_edit", "", "", 0, 101, 0, 201,
            0, 0, 0, 0, "", "", "", "sent", "{}", 1,
            1000.0, 1001.0, 1001.0, 0,
        ),
    )
    conn.execute(
        "INSERT INTO command_attempt_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (1, "attempt:1", 1, "reply_new", 201, 0, "test", "abc", "live", "e1", 1002.0,
         json.dumps({"bind_reason": "exact_reply_to_root", "bind_anchor": "reply_to_msg_id"})),
    )
    conn.commit()
    conn.close()


def test_checkpoint_reports_durable_sent_parity_and_strong_binding(tmp_path):
    db_path = tmp_path / "state.db"
    messages_dir = tmp_path / "messages"
    messages_dir.mkdir()
    _prepare_db(db_path)
    (messages_dir / "1970-01-01.log").write_text(
        json.dumps({
            "ts": "1970-01-01 08:16:41 UTC+8",
            "event_type": "sent",
            "message_id": 101,
            "text": ".测试",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report = build_checkpoint(db_path=db_path, messages_dir=messages_dir, now=1100.0)

    assert report["status"] == "ok"
    assert report["sent_log_parity"]["missing_root_count"] == 0
    assert report["binding"]["bind_reason"] == {"exact_reply_to_root": 1}
    assert report["binding"]["non_strong_written_bindings"] == 0
    assert report["attempts"]["send_unknown"] == 0


def test_checkpoint_warns_on_missing_root_and_non_strong_binding(tmp_path):
    db_path = tmp_path / "state.db"
    messages_dir = tmp_path / "messages"
    messages_dir.mkdir()
    _prepare_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE command_attempt_evidence SET payload_json = ?",
        (json.dumps({"bind_reason": "candidate_only", "bind_anchor": "identity_family_time"}),),
    )
    conn.commit()
    conn.close()

    report = build_checkpoint(db_path=db_path, messages_dir=messages_dir, now=1100.0)

    assert report["status"] == "warn"
    assert report["sent_log_parity"]["missing_root_ids"] == [101]
    assert report["binding"]["non_strong_written_bindings"] == 1


def test_checkpoint_excludes_attempts_older_than_retained_message_log_coverage(tmp_path):
    db_path = tmp_path / "state.db"
    messages_dir = tmp_path / "messages"
    messages_dir.mkdir()
    _prepare_db(db_path)
    (messages_dir / "1970-01-01.log").write_text(
        json.dumps({
            "ts": "1970-01-01 08:17:30 UTC+8",
            "event_type": "message",
            "message_id": 999,
            "text": "retained log starts after the attempt",
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    report = build_checkpoint(db_path=db_path, messages_dir=messages_dir, now=1100.0)

    assert report["status"] == "ok"
    assert report["sent_log_parity"]["excluded_before_log_coverage"] == 1
    assert report["sent_log_parity"]["rooted_attempts"] == 0
    assert report["sent_log_parity"]["missing_root_count"] == 0


def test_checkpoint_keeps_old_attempt_anomalies_visible_without_current_warning(tmp_path):
    db_path = tmp_path / "state.db"
    messages_dir = tmp_path / "messages"
    messages_dir.mkdir()
    _prepare_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE command_attempts SET transport = 'queued', root_msg_id = 0, last_error = 'old failure', updated_at = 1000"
    )
    conn.commit()
    conn.close()

    report = build_checkpoint(db_path=db_path, messages_dir=messages_dir, now=100000.0)

    assert report["status"] == "ok"
    assert report["attempts"]["stale_transport_over_300s"] == 1
    assert report["attempts"]["last_error_count"] == 1
    assert report["attempts"]["recent_stale_transport_over_300s"] == 0
    assert report["attempts"]["recent_last_error_count"] == 0


def test_checkpoint_warns_on_recent_attempt_anomalies(tmp_path):
    db_path = tmp_path / "state.db"
    messages_dir = tmp_path / "messages"
    messages_dir.mkdir()
    _prepare_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "UPDATE command_attempts SET transport = 'queued', root_msg_id = 0, last_error = 'new failure', updated_at = 99000"
    )
    conn.commit()
    conn.close()

    report = build_checkpoint(db_path=db_path, messages_dir=messages_dir, now=100000.0)

    assert report["status"] == "warn"
    assert report["attempts"]["recent_stale_transport_over_300s"] == 1
    assert report["attempts"]["recent_last_error_count"] == 1
