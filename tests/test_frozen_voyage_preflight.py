import json
import sqlite3

import pytest

from tools import defensive_preflight as preflight
from test_defensive_preflight import _create_preflight_db


NOW = 1_700_000_000.0
ENTRY = "https://t.me/fanrenxiuxian_bot?startapp=df_PRIVATE_FIXTURE"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    _create_preflight_db(path, identity_enabled=False)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
            ALTER TABLE identity_module_state ADD COLUMN concubine_voyage_enabled INTEGER DEFAULT 1;
            ALTER TABLE identity_timers ADD COLUMN next_concubine_time REAL DEFAULT 1;
            ALTER TABLE identity_runtime_state ADD COLUMN concubine_phase TEXT DEFAULT 'idle';
            ALTER TABLE identity_runtime_state ADD COLUMN concubine_voyage_status TEXT DEFAULT 'sailing';
            ALTER TABLE identity_runtime_state ADD COLUMN concubine_voyage_return_at REAL DEFAULT 2;
            ALTER TABLE identity_runtime_state ADD COLUMN concubine_last_snapshot_at REAL DEFAULT 1;
        """)
        conn.executemany("INSERT INTO meta VALUES (?, ?)", [
            ("global_enabled", "1"),
            ("channel_send_as_health", json.dumps({"status": "closed", "restore_identity_ids": [1]})),
            ("miniapp_auto_config", json.dumps({"cave_public_entry_urls": [ENTRY]})),
        ])
    monkeypatch.setattr(preflight, "DB_PATH", path)
    monkeypatch.setattr(preflight.time, "time", lambda: NOW)
    monkeypatch.setattr(preflight, "_listener_status", lambda now: {"level": "healthy", "module": "listener"})
    return path


def observed():
    return [row for row in preflight.snapshot(horizon_sec=21600)["checks"] if row["module"] == "concubine_public"]


def test_frozen_voyage_is_visible_without_network_or_writes(db, monkeypatch):
    before = db.read_bytes()
    monkeypatch.setattr(preflight.subprocess, "run", lambda *a, **k: pytest.fail("unexpected subprocess"))
    rows = observed()
    assert len(rows) == 1
    row = rows[0]
    assert row["send_as_id"] == 1 and row["level"] == "watch"
    assert row["local_snapshot_only"] is True
    assert row["local_times"]["concubine_voyage_return_at"] == 2
    assert ENTRY not in json.dumps(rows)
    assert db.read_bytes() == before


@pytest.mark.parametrize("mode", ["manual_disabled", "channel_open", "global_off", "module_off", "role_enabled", "entry_missing"])
def test_deliberately_inactive_or_other_routes_are_not_flagged(db, mode):
    with sqlite3.connect(db) as conn:
        if mode in {"manual_disabled", "channel_open"}:
            value = {"status": "open" if mode == "channel_open" else "closed", "restore_identity_ids": []}
            conn.execute("UPDATE meta SET value=? WHERE key='channel_send_as_health'", (json.dumps(value),))
        elif mode == "global_off":
            conn.execute("UPDATE meta SET value='0' WHERE key='global_enabled'")
        elif mode == "entry_missing":
            conn.execute("UPDATE meta SET value='{}' WHERE key='miniapp_auto_config'")
        elif mode == "module_off":
            conn.execute("UPDATE identity_module_state SET concubine_voyage_enabled=0")
        else:
            conn.execute("UPDATE identities SET enabled=1")
    assert observed() == []


@pytest.mark.parametrize("ids,expected", [(["1"], 1), ([True], 0), ({"1": True}, 0), ("1", 0), ([-1], 0), (None, 0)])
def test_restore_membership_is_explicit(db, ids, expected):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE meta SET value=? WHERE key='channel_send_as_health'",
                     (json.dumps({"status": "closed", "restore_identity_ids": ids}),))
    assert len(observed()) == expected


@pytest.mark.parametrize("timestamp", [0, -1, "broken", float("inf"), NOW + 86400])
def test_missing_invalid_and_future_timers_never_claim_server_readiness(db, timestamp):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE identity_runtime_state SET concubine_voyage_return_at=?", (timestamp,))
    row = observed()[0]
    assert row["level"] == "watch" and row["local_snapshot_only"] is True
    assert row["local_times"]["concubine_voyage_return_at"] == (timestamp if timestamp == NOW + 86400 else 0)


def test_legacy_database_without_new_columns_is_unchanged(tmp_path):
    path = tmp_path / "legacy.db"
    _create_preflight_db(path, identity_enabled=False)
    before = path.read_bytes()
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as conn:
        assert preflight._frozen_voyage_checks(conn, NOW) == []
    assert path.read_bytes() == before


@pytest.mark.parametrize("key,value", [
    ("global_enabled", "invalid"),
    ("global_enabled", '"1"'),
    ("global_enabled", "1.0"),
    ("channel_send_as_health", "[]"),
    ("channel_send_as_health", "invalid"),
    ("miniapp_auto_config", "null"),
    ("miniapp_auto_config", "[]"),
    ("miniapp_auto_config", "invalid"),
])
def test_malformed_meta_does_not_interrupt_other_checks(db, key, value):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE meta SET value=? WHERE key=?", (value, key))
    data = preflight.snapshot(horizon_sec=21600)
    assert not any(row["module"] == "concubine_public" for row in data["checks"])
    assert any(row["module"] == "pending_tasks" for row in data["checks"])


@pytest.mark.parametrize("entry", ["  \n\t", [""], [None], [False], [123], {"url": ENTRY}, True])
def test_blank_or_wrong_type_entry_is_not_configured(db, entry):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE meta SET value=? WHERE key='miniapp_auto_config'",
                     (json.dumps({"cave_public_entry_urls": entry}),))
    assert observed() == []


@pytest.mark.parametrize("config", [
    {"cave_public_entry_url": ENTRY},
    {"cave_public_entry_urls": ENTRY},
    {"cave_public_entry_urls": ["", ENTRY]},
    {"cave_public_entry_urls": [], "cave_public_entry_url": ENTRY},
])
def test_existing_entry_config_forms_remain_supported(db, config):
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE meta SET value=? WHERE key='miniapp_auto_config'", (json.dumps(config),))
    assert len(observed()) == 1


def test_meta_and_identity_rows_share_one_read_snapshot(db, monkeypatch):
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
    fetch_rows = preflight._fetch_rows
    changed = False

    def update_channel_between_reads(conn, sql, *args):
        nonlocal changed
        if not changed:
            changed = True
            with sqlite3.connect(db) as writer:
                writer.execute("UPDATE meta SET value=? WHERE key='channel_send_as_health'",
                               (json.dumps({"status": "open", "restore_identity_ids": []}),))
                writer.execute("UPDATE identities SET enabled=1")
        return fetch_rows(conn, sql, *args)

    monkeypatch.setattr(preflight, "_fetch_rows", update_channel_between_reads)
    assert len(observed()) == 1
    assert changed
    assert observed() == []
