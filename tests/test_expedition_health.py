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


