import copy
import json
import sqlite3

import pytest

from tools import health_observer as observer


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "state.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE identities(send_as_id INTEGER PRIMARY KEY, username TEXT, enabled INTEGER);
            CREATE TABLE identity_module_state(
                send_as_id INTEGER PRIMARY KEY, deep_retreat_enabled INTEGER, yuanying_enabled INTEGER);
            CREATE TABLE identity_timers(
                send_as_id INTEGER PRIMARY KEY, next_deep_retreat_time REAL,
                next_yuanying_time REAL, next_concubine_time REAL DEFAULT 0);
            CREATE TABLE identity_runtime_state(
                send_as_id INTEGER PRIMARY KEY,
                deep_retreat_phase TEXT DEFAULT 'running', yuanying_phase TEXT DEFAULT 'running',
                concubine_phase TEXT DEFAULT 'idle',
                deep_retreat_probe_pending INTEGER DEFAULT 0, yuanying_probe_pending INTEGER DEFAULT 0,
                deep_retreat_summary_sent_at REAL DEFAULT 0, yuanying_summary_sent_at REAL DEFAULT 0,
                last_deep_retreat_command_time REAL DEFAULT 0,
                last_deep_retreat_summary_msg_id INTEGER DEFAULT 0, last_yuanying_summary_msg_id INTEGER DEFAULT 0,
                tower_reply_due_at REAL DEFAULT 0, last_tower_msg_id INTEGER DEFAULT 0);
            CREATE TABLE pending_tasks(send_as_id INTEGER, cmd TEXT, sent_at REAL, timeout REAL,
                retry INTEGER, max_retry INTEGER, source_module TEXT);
            INSERT INTO identities VALUES (101, 'frozen', 0), (102, 'manual_off', 0), (103, 'enabled', 1);
            INSERT INTO identity_module_state VALUES (101, 1, 1), (102, 1, 1), (103, 1, 1);
            INSERT INTO identity_timers VALUES (101, 90000, 110000, 0), (102, 90000, 90000, 0), (103, 110000, 110000, 0);
            INSERT INTO identity_runtime_state(send_as_id) VALUES (101), (102), (103);
        """)
        put_meta(conn, "channel_send_as_health", {"status": "closed", "restore_identity_ids": [101, 103, 999]})
        put_meta(conn, "miniapp_auto_config", {
            "cave_public_entry_url": "https://t.me/fanrenxiuxian_bot?startapp=df_test",
            "cave_public_deep_status_enabled": True, "cave_public_yuanying_enabled": True,
        })
        put_meta(conn, "miniapp_state_records", {"101:cave_deep_retreat": {
            "identity_id": 101, "game_key": "cave_deep_retreat", "source": "cave_dwelling_miniapp",
            "updated_at": 89900, "state": {
                "identity_verified": True, "ok": True, "parser_version": 2, "outcome_unknown": False,
                "snapshot": {"known": True, "conflicting": False, "active": True, "end_ms": 120000000},
            },
        }})
    return path


def put_meta(conn, key, value):
    conn.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, json.dumps(value)))


def read_meta(conn, key):
    return json.loads(conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0])


def inspect(db):
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        before = conn.total_changes
        config = read_meta(conn, "miniapp_auto_config")
        original = copy.deepcopy(config)
        result = observer.build_public_phaseful_summary(
            conn, 100000, config=config, restore_ids=[101, 103, 999],
            scheduling_suppressed=False, entry_blocked=False,
        )
        assert config == original
        assert conn.total_changes == before
        return result


def test_frozen_only_cohort_has_separate_readonly_coverage(db):
    rows = inspect(db)
    assert len(rows) == 2
    assert {row["identity_id"] for row in rows} == {101}
    assert [row["reason"] for row in rows] == ["server_running", "local_wait"]
    assert not any(row["needs_review"] for row in rows)
    result = observer.read_db_business_state(db, 100000)
    assert result["available"]
    assert result["public_phaseful_summary"] == rows
    assert {row["identity_id"] for row in result["module_summary"]} == {103}
    assert not result["stuck_phases"]


@pytest.mark.parametrize("status", ["open", "unknown", ""])
def test_stale_restore_list_does_not_monitor_manually_disabled_identities(db, status):
    with sqlite3.connect(db) as conn:
        put_meta(conn, "channel_send_as_health", {"status": status, "restore_identity_ids": [101]})
    assert observer.read_db_business_state(db, 100000)["public_phaseful_summary"] == []


@pytest.mark.parametrize("module", ["deep_retreat", "yuanying"])
@pytest.mark.parametrize("level", ["identity", "public"])
def test_module_switches_exclude_unselected_work(db, module, level):
    with sqlite3.connect(db) as conn:
        if level == "identity":
            conn.execute(f"UPDATE identity_module_state SET {module}_enabled=0 WHERE send_as_id=101")
        else:
            config = read_meta(conn, "miniapp_auto_config")
            config["cave_public_deep_status_enabled" if module == "deep_retreat" else "cave_public_yuanying_enabled"] = False
            put_meta(conn, "miniapp_auto_config", config)
    assert [row["module"] for row in inspect(db)] == (["yuanying"] if module == "deep_retreat" else ["deep_retreat"])


@pytest.mark.parametrize(("key", "value"), [
    ("identity_id", 102), ("identity_id", "101"), ("game_key", "other"),
    ("source", "untrusted"), ("updated_at", 0), ("updated_at", 100001),
    ("updated_at", float("inf")), ("state", []),
])
def test_unbound_or_invalid_record_cannot_prove_running(db, key, value):
    with sqlite3.connect(db) as conn:
        records = read_meta(conn, "miniapp_state_records")
        records["101:cave_deep_retreat"][key] = value
        put_meta(conn, "miniapp_state_records", records)
    row = inspect(db)[0]
    assert row["reason"] == "due_unverified"
    assert row["needs_review"]


@pytest.mark.parametrize(("key", "value"), [
    ("identity_verified", False), ("identity_verified", "true"), ("ok", False),
    ("parser_version", 1), ("snapshot", None),
    ("snapshot", {"active": True, "end_ms": float("inf"), "known": True, "conflicting": False}),
    ("snapshot", {"active": True, "end_ms": 120000000, "known": True, "conflicting": True}),
    ("snapshot", {"active": True, "end_ms": 120000000, "known": True, "conflicting": False, "completed": True}),
    ("snapshot", {"active": True, "end_ms": 120000000, "known": True, "conflicting": False, "can_settle": True}),
])
def test_invalid_snapshot_does_not_suppress_review(db, key, value):
    with sqlite3.connect(db) as conn:
        records = read_meta(conn, "miniapp_state_records")
        records["101:cave_deep_retreat"]["state"][key] = value
        put_meta(conn, "miniapp_state_records", records)
    assert inspect(db)[0]["reason"] == "due_unverified"


@pytest.mark.parametrize(("field", "value"), [
    ("last_deep_retreat_command_time", 91000),
    ("deep_retreat_phase", "summary_due"), ("deep_retreat_phase", "post_summary_wait"),
    ("deep_retreat_phase", "idle"),
])
def test_old_running_snapshot_cannot_override_newer_operation_or_phase(db, field, value):
    with sqlite3.connect(db) as conn:
        conn.execute(f"UPDATE identity_runtime_state SET {field}=? WHERE send_as_id=101", (value,))
    assert inspect(db)[0]["reason"] == "due_unverified"


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("outcome_unknown", True, "result_unknown"),
    ("deep_retreat_probe_pending", 1, "pending"),
    ("last_deep_retreat_summary_msg_id", 42, "pending"),
    ("deep_retreat_phase", "launching", "pending"),
])
def test_unknown_and_pending_precede_old_success_snapshot(db, field, value, reason):
    with sqlite3.connect(db) as conn:
        if field == "outcome_unknown":
            records = read_meta(conn, "miniapp_state_records")
            records["101:cave_deep_retreat"]["state"][field] = value
            put_meta(conn, "miniapp_state_records", records)
        else:
            conn.execute(f"UPDATE identity_runtime_state SET {field}=? WHERE send_as_id=101", (value,))
    row = inspect(db)[0]
    assert row["reason"] == reason
    assert row["needs_review"]


@pytest.mark.parametrize(("setting", "reason"), [
    ("paused", "scheduling_suppressed"), ("recovery", "scheduling_suppressed"),
    ("missing", "entry_missing"), ("blocked", "entry_blocked"), ("retry", "shared_retry_after"),
])
def test_dependencies_remain_visible_without_stall_alert(db, setting, reason):
    with sqlite3.connect(db) as conn:
        config = read_meta(conn, "miniapp_auto_config")
        if setting == "paused":
            put_meta(conn, "global_enabled", 0)
        elif setting == "recovery":
            put_meta(conn, "global_recovery_hold_until", 110000)
        elif setting == "missing":
            config["cave_public_entry_url"] = ""
        elif setting == "blocked":
            config["cave_public_entry_token_blocked_signature"] = observer._cave_public_entry_urls_signature(
                observer._normalize_cave_public_entry_urls(config["cave_public_entry_url"]))
        elif setting == "retry":
            config["cave_public_shared_retry_at"] = 110000
        put_meta(conn, "miniapp_auto_config", config)
    result = observer.read_db_business_state(db, 100000)
    assert {row["reason"] for row in result["public_phaseful_summary"]} == {reason}
    assert not any(row["needs_review"] for row in result["public_phaseful_summary"])


@pytest.mark.parametrize(("due", "reason", "review"), [
    (0, "schedule_evidence_missing", False), (float("inf"), "schedule_evidence_missing", False),
    (100010, "local_wait", False), (100000, "due_unverified", False),
    (100000 - observer.PUBLIC_PHASEFUL_REVIEW_SEC, "due_unverified", False),
    (99999 - observer.PUBLIC_PHASEFUL_REVIEW_SEC, "due_unverified", True),
])
def test_local_deadline_is_not_proof_of_success_or_a_stall(db, due, reason, review):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE identity_timers SET next_yuanying_time=? WHERE send_as_id=101", (due,))
    row = inspect(db)[1]
    assert row["reason"] == reason
    assert row["needs_review"] is review
    assert row["evidence_scope"] == "persisted_only_worker_backoff_unavailable"


@pytest.mark.parametrize("recovery", [False, True])
def test_maintenance_pause_does_not_hide_allowed_public_http(db, recovery):
    with sqlite3.connect(db) as conn:
        put_meta(conn, "global_enabled", 0)
        conn.execute("INSERT INTO meta VALUES ('global_pause_source', 'tianzun_maintenance')")
        if recovery:
            put_meta(conn, "global_recovery_throttle_until", 110000)
    rows = observer.read_db_business_state(db, 100000)["public_phaseful_summary"]
    assert [row["reason"] for row in rows] == (
        ["scheduling_suppressed"] * 2 if recovery else ["server_running", "local_wait"]
    )


def test_overdue_review_is_warning_not_stuck_phase_and_excludes_secrets(db):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE identity_timers SET next_yuanying_time=80000 WHERE send_as_id=101")
        put_meta(conn, "miniapp_state_records", {"101:cave_yuanying": {
            "identity_id": 101, "game_key": "cave_yuanying", "updated_at": 80000,
            "state": {"status": "unknown", "outcome_unknown": True, "token": "private-sentinel"},
        }})
    result = observer.read_db_business_state(db, 100000)
    assert not result["stuck_phases"]
    alert = next(row for row in result["alerts"] if "public phaseful" in row["message"])
    assert alert["severity"] == "warn"
    assert "not proof of a stall" in alert["message"]
    assert "private-sentinel" not in json.dumps(result)
