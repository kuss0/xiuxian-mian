from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from tools import trial_receipt_inventory as report


def record(identity=1, account=100, op="a", receipts=2):
    return {
        "version": 1, "operation_id": op * 32, "identity_id": identity,
        "account_id": account, "player_id": identity, "created_at": 1791240000,
        "updated_at": 1791240100, "revision": 5, "pending_save": False,
        "checkpoint": {
            "version": 1, "phase": "complete", "sequence": 5,
            "action_dispatched": bool(receipts), "pending": {}, "outcome_unknown": False,
            "round_receipts": [{"round_key": f"{index:064x}", "data": {"traceGain": 3}}
                               for index in range(receipts)],
            "request_resolution": {}, "retry_after_sec": 0, "status": "settled", "error": "",
        },
    }


def row(identity=1, current=None, archive=None):
    return {"send_as_id": identity, "identity_present": 1,
            "trial_operation": json.dumps(current if current is not None else record(identity)),
            "trial_operation_archive": json.dumps(archive or [])}


def archived(current):
    return {
        "version": 1, "operation_id": current["operation_id"], "identity_id": current["identity_id"],
        "account_id": current["account_id"], "source_day": "2026-10-05", "archived_for_day": "2026-10-06",
        "archived_at": 1791240200, "reason": "cross_day_outcome_unknown", "record": deepcopy(current),
    }


def test_reuses_validator_but_does_not_claim_batch_or_player_ownership():
    assert report.operations.valid_record(record())
    result = report.build_report([row()], {"1": 100})
    assert result["status"] == "ok"
    assert result["counts"]["current_valid_receipts"] == 2
    assert result["counts"]["current_owned"] == 1
    assert result["identities"][0]["current"]["player_id_present"]
    for field in ("batch_ownership_verified", "historical_completeness_verified",
                  "player_binding_verified", "reward_totals_verified"):
        assert result[field] is False


@pytest.mark.parametrize("raw", ["null", "[]", "", "broken", '{"token":"DO_NOT_PRINT"}',
                               '{"version":1,"version":1}', '"DO_NOT_PRINT"', None])
def test_bad_records_are_visible_and_secret_free(raw):
    value = row()
    value["trial_operation"] = raw
    result = report.build_report([value], {"1": 100})
    assert result["status"] == "watch"
    assert result["counts"]["current_invalid"] == 1
    assert "DO_NOT_PRINT" not in json.dumps(result)


@pytest.mark.parametrize("accounts", [{}, {"1": True}, {"1": "100"}, {"1": -1}, {"1": 200}])
def test_missing_or_changed_account_is_not_defaulted_to_runtime_owner(accounts):
    result = report.build_report([row()], accounts)
    assert result["counts"]["current_owned"] == 0
    assert result["status"] == "watch"


def test_mismatched_identity_is_reported_even_with_valid_account():
    result = report.build_report([row(current=record(identity=2))], {"1": 100})
    assert "current_owner_mismatch" in result["identities"][0]["warnings"]


def test_empty_is_not_missing_and_zero_receipts_do_not_invent_rewards():
    result = report.build_report([row(current={}), row(2, record(2, receipts=0))], {"1": 100, "2": 100})
    assert result["counts"]["current_empty"] == 1
    assert result["counts"]["current_valid_receipts"] == 0
    assert "gains" not in result


def test_same_rewards_and_round_keys_under_distinct_owners_are_not_collapsed():
    result = report.build_report([row(), row(2, record(2, op="b"))], {"1": 100, "2": 100})
    assert result["status"] == "ok" and result["counts"]["current_valid_receipts"] == 4
    assert not any(result["conflicts"].values())


def test_operation_id_collision_is_not_treated_as_deduplication():
    result = report.build_report([row(), row(2)], {"1": 100, "2": 100})
    assert result["conflicts"]["operation_owner_conflict"] == 1
    assert result["status"] == "watch"


def test_archive_prefix_growth_is_not_double_counted_as_current_receipts():
    old = record(receipts=1)
    result = report.build_report([row(archive=[archived(old)])], {"1": 100})
    assert not any(result["conflicts"].values())
    assert result["counts"]["current_valid_receipts"] == 2
    assert result["counts"]["archive_valid_receipts"] == 1


def test_changed_receipt_content_is_a_conflict_not_a_larger_reward():
    old = record(receipts=1)
    old["checkpoint"]["round_receipts"][0]["data"]["traceGain"] = 4
    result = report.build_report([row(archive=[archived(old)])], {"1": 100})
    assert result["conflicts"]["operation_receipt_prefix_conflict"] == 1
    assert result["status"] == "watch"


def test_round_key_reused_by_another_operation_is_ambiguous():
    result = report.build_report([row(archive=[archived(record(op="b", receipts=1))])], {"1": 100})
    assert result["conflicts"]["round_key_in_multiple_operations"] == 1
    assert result["status"] == "watch"


def test_historical_account_is_not_assumed_to_be_current_owner():
    result = report.build_report([row(archive=[archived(record(account=200, op="b"))])], {"1": 100})
    assert result["status"] == "ok" and result["counts"]["current_owned"] == 1
    assert result["batch_ownership_verified"] is False


def test_archive_identity_mismatch_remains_visible():
    result = report.build_report([row(archive=[archived(record(identity=2, op="b"))])], {"1": 100})
    assert "archive_identity_mismatch" in result["identities"][0]["warnings"]


@pytest.mark.parametrize("raw", ["{}", "null", '[{"token":"DO_NOT_PRINT"}]'])
def test_invalid_archive_cannot_be_silently_skipped(raw):
    value = row()
    value["trial_operation_archive"] = raw
    result = report.build_report([value], {"1": 100})
    assert "archive_invalid" in result["identities"][0]["warnings"]
    assert "DO_NOT_PRINT" not in json.dumps(result)


def test_pending_save_and_unknown_are_not_claimed_as_success():
    current = record()
    current["pending_save"] = True
    current["checkpoint"]["pending"] = {"action": "finish", "entry_key": "c" * 64, "challenge_key": "d" * 64}
    current["checkpoint"]["outcome_unknown"] = True
    result = report.build_report([row(current=current)], {"1": 100})
    assert result["counts"]["current_pending_save"] == 1
    assert result["counts"]["current_pending_request"] == 1
    assert result["reward_totals_verified"] is False


def test_capacity_and_input_isolation():
    value = row()
    original = deepcopy(value)
    result = report.build_report([value], {"1": 100})
    assert result["capacity"]["current_encoded_bytes"] == len(value["trial_operation"].encode())
    assert result["capacity"]["archive_encoded_bytes"] == 2
    assert value == original
    value["trial_operation"] = "x" * (report.operations.TRIAL_OPERATION_MAX_BYTES + 1)
    assert report.build_report([value], {"1": 100})["counts"]["current_invalid"] == 1


def database(path):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE identities (send_as_id INTEGER PRIMARY KEY);
            CREATE TABLE identity_runtime_state (send_as_id INTEGER PRIMARY KEY, trial_operation TEXT, trial_operation_archive TEXT);
            INSERT INTO meta VALUES ('identity_account_map', '{"1":100}');
            INSERT INTO identities VALUES (1);
        """)
        item = row()
        conn.execute("INSERT INTO identity_runtime_state VALUES (?,?,?)",
                     (item["send_as_id"], item["trial_operation"], item["trial_operation_archive"]))


def test_sqlite_read_only_includes_missing_and_orphan_runtime_rows(tmp_path):
    path = tmp_path / "state.db"
    database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO identities VALUES (2)")
        conn.execute("INSERT INTO identity_runtime_state VALUES (3, '{}', '[]')")
    before = path.read_bytes()
    rows, accounts = report.read_snapshot(path)
    assert {item["send_as_id"] for item in rows} == {1, 2, 3}
    assert report.build_report(rows, accounts)["status"] == "watch"
    assert path.read_bytes() == before


def test_missing_database_is_not_created(tmp_path, capsys):
    path = tmp_path / "missing.db"
    assert report.main(["--db", str(path)]) == 2
    assert not path.exists()
    assert json.loads(capsys.readouterr().out)["reason"] == "snapshot_unavailable_or_invalid"


def test_cli_uses_isolated_test_environment_without_starting_runtime(tmp_path):
    path = tmp_path / "state.db"
    database(path)
    before = path.read_bytes()
    proc = subprocess.run([sys.executable, str(Path(report.__file__)), "--db", str(path)],
                          cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["counts"]["current_valid_receipts"] == 2
    assert path.read_bytes() == before


def test_empty_orphan_is_not_hidden_by_empty_operation(tmp_path):
    path = tmp_path / "state.db"
    database(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO identity_runtime_state VALUES (2, '{}', '[]')")
    rows, accounts = report.read_snapshot(path)
    result = report.build_report(rows, accounts)
    assert result["status"] == "watch"
    assert result["identities"][1]["warnings"] == ["orphan_runtime_row"]


def test_wal_committed_evidence_is_visible(tmp_path):
    path = tmp_path / "state.db"
    database(path)
    with sqlite3.connect(path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE identity_runtime_state SET trial_operation=?", (json.dumps(record(receipts=3)),))
        writer.commit()
        wal = Path(str(path) + "-wal")
        before = wal.read_bytes()
        rows, accounts = report.read_snapshot(path)
        assert report.build_report(rows, accounts)["counts"]["current_valid_receipts"] == 3
        assert wal.read_bytes() == before


def test_snapshot_reads_ownership_and_receipts_in_same_transaction(tmp_path, monkeypatch):
    path = tmp_path / "state.db"
    database(path)
    connect = sqlite3.connect
    with connect(path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")

        class ConcurrentChange(sqlite3.Connection):
            def execute(self, sql, *args):
                cursor = super().execute(sql, *args)
                if sql.startswith("SELECT value FROM meta"):
                    writer.execute("UPDATE meta SET value=?", ('{"1":200}',))
                    writer.execute("UPDATE identity_runtime_state SET trial_operation=?",
                                   (json.dumps(record(account=200)),))
                    writer.commit()
                return cursor

        def open_reader(*args, **kwargs):
            return connect(*args, **kwargs, factory=ConcurrentChange)

        monkeypatch.setattr(report.sqlite3, "connect", open_reader)
        rows, accounts = report.read_snapshot(path)
        assert accounts == {"1": 100}
        assert json.loads(rows[0]["trial_operation"])["account_id"] == 100
        assert report.build_report(rows, accounts)["status"] == "ok"


@pytest.mark.parametrize("rows", [[row(), row()], [{**row(), "send_as_id": True}],
                                  [{**row(), "identity_present": None}]])
def test_bad_snapshot_identity_rows_are_rejected(rows):
    with pytest.raises(ValueError):
        report.build_report(rows, {"1": 100})


def test_invalid_capacity_remains_measured_or_explicitly_unknown():
    item = row()
    item["trial_operation"] = "DO_NOT_PRINT"
    item["trial_operation_archive"] = None
    result = report.build_report([item], {"1": 100})
    assert result["capacity"]["current_encoded_bytes"] == len("DO_NOT_PRINT")
    assert result["capacity"]["current_unmeasured_rows"] == 0
    assert result["capacity"]["archive_unmeasured_rows"] == 1
    assert "DO_NOT_PRINT" not in json.dumps(result)
