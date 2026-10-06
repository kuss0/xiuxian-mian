#!/usr/bin/env python3
"""Read-only inventory of retained trial slots, not a reward or retry ledger."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from model.features import trial_operations as operations


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _decode(raw, limit):
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > limit:
            raise ValueError("oversized record")
        return json.loads(raw, object_pairs_hook=_unique_object)
    return raw


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _size(raw):
    return len((raw if isinstance(raw, str) else _encoded(raw)).encode("utf-8"))


def read_snapshot(db_path):
    """Read both identity ownership and slots from one WAL-aware transaction."""
    uri = Path(db_path).resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        row = conn.execute("SELECT value FROM meta WHERE key='identity_account_map'").fetchone()
        accounts = json.loads(row[0], object_pairs_hook=_unique_object) if row else {}
        if not isinstance(accounts, dict):
            raise ValueError("invalid account map")
        rows = [dict(item) for item in conn.execute("""
            SELECT i.send_as_id, 1 AS identity_present, r.trial_operation, r.trial_operation_archive
            FROM identities i LEFT JOIN identity_runtime_state r USING(send_as_id)
            UNION ALL
            SELECT r.send_as_id, 0 AS identity_present, r.trial_operation, r.trial_operation_archive
            FROM identity_runtime_state r LEFT JOIN identities i USING(send_as_id)
            WHERE i.send_as_id IS NULL
        """)]
        return rows, accounts


def _checked(raw, validator, limit, empty):
    size = None
    try:
        if raw is not None:
            size = _size(raw)
        value = _decode(raw, limit)
        if value == empty and type(value) is type(empty):
            return value, size, "empty"
        if validator(value):
            return value, size, "valid"
    except (ValueError, TypeError, OverflowError, RecursionError, KeyError, AttributeError):
        pass
    # Invalid payloads and exception text must never escape into the report.
    return None, size, "invalid"


def build_report(rows, accounts):
    if not isinstance(accounts, dict):
        raise ValueError("invalid account map")
    identities, ids = [], set()
    by_operation = defaultdict(list)
    by_round = defaultdict(set)
    counts = Counter()
    capacity = {
        "current_limit_bytes_per_identity": operations.TRIAL_OPERATION_MAX_BYTES,
        "archive_limit_bytes_per_identity": operations.TRIAL_OPERATION_ARCHIVE_MAX_BYTES,
        "current_encoded_bytes": 0, "archive_encoded_bytes": 0,
        "current_unmeasured_rows": 0, "archive_unmeasured_rows": 0,
        "largest_current_encoded_bytes": 0, "largest_archive_encoded_bytes": 0,
    }

    def collect(record):
        owner = (record["identity_id"], record["account_id"], record["player_id"])
        by_operation[record["operation_id"]].append((owner, record["checkpoint"]["round_receipts"]))
        for receipt in record["checkpoint"]["round_receipts"]:
            by_round[(*owner, receipt["round_key"])].add(record["operation_id"])

    for row in rows:
        identity_id = row["send_as_id"]
        if type(identity_id) is not int or identity_id <= 0 or identity_id in ids:
            raise ValueError("invalid identity rows")
        ids.add(identity_id)
        item = {"identity_id": identity_id, "warnings": []}
        identity_present = row.get("identity_present")
        if type(identity_present) is not int or identity_present not in (0, 1):
            raise ValueError("invalid identity presence")
        if not identity_present:
            item["warnings"].append("orphan_runtime_row")
        current, size, status = _checked(row.get("trial_operation"), operations.valid_record,
                                         operations.TRIAL_OPERATION_MAX_BYTES, {})
        item["current"] = {"validation": status, "encoded_bytes": size}
        counts["current_" + status] += 1
        if status == "invalid":
            item["warnings"].append("current_invalid")
        if size is not None:
            capacity["current_encoded_bytes"] += size
            capacity["largest_current_encoded_bytes"] = max(capacity["largest_current_encoded_bytes"], size)
        else:
            capacity["current_unmeasured_rows"] += 1
        if status == "valid":
            checkpoint = current["checkpoint"]
            account = accounts.get(str(identity_id))
            known_account = type(account) is int and account >= 0
            owned = (bool(identity_present) and known_account and current["identity_id"] == identity_id
                     and current["account_id"] == account)
            item["current"].update(
                account_binding_verified=owned, player_id_present=current["player_id"] is not None,
                phase=checkpoint["phase"], pending_save=current["pending_save"],
                pending=bool(checkpoint["pending"]), receipts=len(checkpoint["round_receipts"]),
            )
            if not owned:
                item["warnings"].append("current_owner_mismatch" if known_account else "account_binding_missing")
            counts["current_owned"] += int(owned)
            counts["current_valid_receipts"] += len(checkpoint["round_receipts"])
            counts["current_pending_save"] += int(current["pending_save"])
            counts["current_pending_request"] += int(bool(checkpoint["pending"]))
            counts["current_complete_phase"] += int(checkpoint["phase"] == "complete")
            collect(current)

        archive, size, status = _checked(row.get("trial_operation_archive"), operations.valid_archive,
                                         operations.TRIAL_OPERATION_ARCHIVE_MAX_BYTES, [])
        item["archive"] = {"validation": status, "encoded_bytes": size}
        counts["archive_" + status] += 1
        if status == "invalid":
            item["warnings"].append("archive_invalid")
        if size is not None:
            capacity["archive_encoded_bytes"] += size
            capacity["largest_archive_encoded_bytes"] = max(capacity["largest_archive_encoded_bytes"], size)
        else:
            capacity["archive_unmeasured_rows"] += 1
        if status == "valid":
            item["archive"]["entries"] = len(archive)
            counts["archive_entries"] += len(archive)
            for entry in archive:
                if entry["identity_id"] != identity_id:
                    item["warnings"].append("archive_identity_mismatch")
                # An old account binding is historical evidence, not permission
                # to adopt its resources into the current owner's next batch.
                collect(entry["record"])
                counts["archive_valid_receipts"] += len(entry["record"]["checkpoint"]["round_receipts"])
        identities.append(item)

    conflicts = Counter()
    for records in by_operation.values():
        owners = {owner for owner, _ in records}
        if len(owners) != 1:
            conflicts["operation_owner_conflict"] += 1
        prefixes = sorted((receipts for _, receipts in records), key=len)
        if any(_encoded(left) != _encoded(right[:len(left)]) for left, right in zip(prefixes, prefixes[1:])):
            conflicts["operation_receipt_prefix_conflict"] += 1
    conflicts["round_key_in_multiple_operations"] = sum(len(op_ids) > 1 for op_ids in by_round.values())
    warnings = sum(len(item["warnings"]) for item in identities) + sum(conflicts.values())
    return {
        "version": 1, "status": "watch" if warnings else "ok", "identity_count": len(ids),
        "scope": "retained_current_slots_and_unknown_archives_only",
        "batch_ownership_verified": False, "historical_completeness_verified": False,
        "player_binding_verified": False, "reward_totals_verified": False,
        "counts": dict(counts), "conflicts": dict(conflicts), "capacity": capacity,
        "identities": sorted(identities, key=lambda item: item["identity_id"]),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/state/chaogu_state.db")
    args = parser.parse_args(argv)
    try:
        rows, accounts = read_snapshot(args.db)
        report = build_report(rows, accounts)
    except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError, RecursionError, KeyError):
        print(json.dumps({"status": "error", "reason": "snapshot_unavailable_or_invalid"}))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 1 if report["status"] == "watch" else 0


if __name__ == "__main__":
    raise SystemExit(main())
