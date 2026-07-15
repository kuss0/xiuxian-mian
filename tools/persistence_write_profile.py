#!/usr/bin/env python3
"""Read-only estimate of the current full-state persistence write surface."""

from __future__ import annotations

import argparse
import ast
import json
import os
import sqlite3
from pathlib import Path
from urllib.parse import quote


IDENTITY_MUTATIONS_PER_SAVE = 6


def _readonly_connection(db_path):
    resolved = Path(db_path).resolve()
    uri = f"file:{quote(str(resolved))}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _table_count(conn, table_name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (str(table_name),),
    ).fetchone()
    if not row:
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0] or 0)


def _dict_assignment_size(source_path, variable_name):
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == variable_name for target in targets):
            continue
        value = node.value
        if isinstance(value, ast.Dict):
            return len(value.keys)
    return 0


def _call_counts(source_root, function_names):
    totals = {name: 0 for name in function_names}
    by_file = {}
    for path in sorted(Path(source_root).rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeError):
            continue
        current = {name: 0 for name in function_names}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called_name = ""
            if isinstance(node.func, ast.Name):
                called_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                called_name = node.func.attr
            if called_name in current:
                current[called_name] += 1
                totals[called_name] += 1
        if any(current.values()):
            by_file[str(path.relative_to(source_root))] = current
    return totals, by_file


def build_profile(db_path, *, source_root="model", persistence_path="model/persistence.py", guard_path=""):
    db_path = Path(db_path).resolve()
    source_root = Path(source_root).resolve()
    persistence_path = Path(persistence_path).resolve()
    guard_path = Path(guard_path).resolve() if guard_path else None

    with _readonly_connection(db_path) as conn:
        identity_count = _table_count(conn, "identities")
        pending_count = _table_count(conn, "pending_tasks")
        message_index_count = _table_count(conn, "message_index")
        page_size = int(conn.execute("PRAGMA page_size").fetchone()[0] or 0)
        page_count = int(conn.execute("PRAGMA page_count").fetchone()[0] or 0)

    meta_key_count = _dict_assignment_size(persistence_path, "_META_STATE_CODEC")
    call_totals, call_counts_by_file = _call_counts(source_root, ("save_state", "mark_dirty"))
    minimum_mutating_statements = (
        meta_key_count
        + identity_count * IDENTITY_MUTATIONS_PER_SAVE
        + pending_count
        + message_index_count
    )
    guard_size = 0
    guard_exists = bool(guard_path and guard_path.exists())
    if guard_exists:
        guard_size = int(guard_path.stat().st_size)

    return {
        "policy": "read-only estimate; no state load, writes, sends, or service control",
        "db_path": str(db_path),
        "db_size_bytes": page_size * page_count,
        "identity_count": identity_count,
        "pending_task_count": pending_count,
        "message_index_count": message_index_count,
        "meta_codec_key_count": meta_key_count,
        "identity_mutations_per_full_save": IDENTITY_MUTATIONS_PER_SAVE,
        "minimum_mutating_statements_per_full_save": minimum_mutating_statements,
        "guard_backup": {
            "path": str(guard_path) if guard_path else "",
            "exists": guard_exists,
            "size_bytes": guard_size,
        },
        "source_call_counts": call_totals,
        "source_call_counts_by_file": call_counts_by_file,
        "assumptions": [
            "Each full save writes every meta codec key.",
            "Each identity performs four upserts and two child-table deletes before child inserts.",
            "Schema checks, selects, commits, WAL traffic, and SQLite backup internals are excluded.",
            "Source call counts are static call sites, not runtime invocation frequency.",
        ],
    }


def _format_bytes(value):
    value = max(0, int(value or 0))
    for suffix in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or suffix == "GiB":
            return f"{value:.1f}{suffix}" if suffix != "B" else f"{value}B"
        value /= 1024.0
    return f"{value:.1f}GiB"


def format_profile(payload):
    guard = payload.get("guard_backup") or {}
    calls = payload.get("source_call_counts") or {}
    return "\n".join(
        [
            "persistence write profile",
            f"policy: {payload.get('policy')}",
            f"db: {payload.get('db_path')} ({_format_bytes(payload.get('db_size_bytes'))})",
            (
                "rows: "
                f"identities={payload.get('identity_count')} "
                f"pending={payload.get('pending_task_count')} "
                f"message_index={payload.get('message_index_count')}"
            ),
            f"meta codec keys: {payload.get('meta_codec_key_count')}",
            f"minimum mutating statements/full save: {payload.get('minimum_mutating_statements_per_full_save')}",
            (
                "guard backup: "
                f"{'present' if guard.get('exists') else 'missing'} "
                f"{guard.get('path') or '-'} ({_format_bytes(guard.get('size_bytes'))})"
            ),
            f"static call sites: save_state={calls.get('save_state', 0)} mark_dirty={calls.get('mark_dirty', 0)}",
        ]
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/state/chaogu_state.db")
    parser.add_argument("--source-root", default="model")
    parser.add_argument("--persistence-path", default="model/persistence.py")
    parser.add_argument(
        "--guard-path",
        default=os.environ.get(
            "XIUXIAN_LIVE_GUARD_DB_FILE",
            "/root/xiuxian-main-live-guard/chaogu_state.last-good.db",
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    payload = build_profile(
        args.db,
        source_root=args.source_root,
        persistence_path=args.persistence_path,
        guard_path=args.guard_path,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else format_profile(payload))


if __name__ == "__main__":
    main()
