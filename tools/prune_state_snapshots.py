#!/usr/bin/env python3
"""Prune hand-made chaogu_state rollback snapshots.

Snapshots are created by hand before risky changes (``chaogu_state.before-*``,
``chaogu_state.pre-*``, ``chaogu_state.db.pre-*``). Nothing ever removed them, so
they had grown to 103 files / 417 MiB. This keeps the newest N and drops the
rest, taking the companion ``-shm``/``-wal`` files along with each snapshot.

The live database (``chaogu_state.db``) is never a candidate.

    tools/prune_state_snapshots.py                 # dry-run, prints the plan
    tools/prune_state_snapshots.py --apply         # actually delete
    tools/prune_state_snapshots.py --keep 30 --apply
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


DEFAULT_KEEP = 20
LIVE_DB_NAMES = {"chaogu_state.db", "chaogu_state.db-shm", "chaogu_state.db-wal"}
SNAPSHOT_GLOB = "chaogu_state.*"
SIDECAR_SUFFIXES = ("-shm", "-wal")
RE_STAMP = re.compile(r"(\d{8})(?:[-_]?(\d{6}))?")


def _sort_key(path: Path) -> tuple[str, str, float]:
    """Order snapshots newest-first.

    Prefer the timestamp embedded in the filename (that is what the operator
    reads), and fall back to mtime when a name carries no stamp.
    """
    match = RE_STAMP.search(path.name)
    day = match.group(1) if match else "00000000"
    clock = (match.group(2) or "") if match else ""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (day, clock, mtime)


def collect_snapshots(state_dir: Path) -> list[Path]:
    snapshots = []
    for path in state_dir.glob(SNAPSHOT_GLOB):
        if not path.is_file() or path.name in LIVE_DB_NAMES:
            continue
        if path.name.endswith(SIDECAR_SUFFIXES):
            continue  # handled together with its base snapshot
        snapshots.append(path)
    return sorted(snapshots, key=_sort_key, reverse=True)


def sidecars(path: Path) -> list[Path]:
    return [sibling for suffix in SIDECAR_SUFFIXES if (sibling := path.with_name(path.name + suffix)).exists()]


def _mib(size: int) -> str:
    return f"{size / 1048576:.1f} MiB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--state-dir", default=str(Path(__file__).resolve().parents[1] / "data" / "state"))
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help=f"snapshots to retain (default {DEFAULT_KEEP})")
    parser.add_argument("--apply", action="store_true", help="delete files; without it this is a dry run")
    args = parser.parse_args(argv)

    if args.keep < 1:
        print("--keep must be >= 1", file=sys.stderr)
        return 2

    state_dir = Path(args.state_dir).expanduser()
    if not state_dir.is_dir():
        print(f"state dir not found: {state_dir}", file=sys.stderr)
        return 2

    snapshots = collect_snapshots(state_dir)
    keep, drop = snapshots[: args.keep], snapshots[args.keep :]

    kept_bytes = sum(path.stat().st_size for path in keep)
    dropped_bytes = 0
    removed = 0
    for path in drop:
        group = [path, *sidecars(path)]
        dropped_bytes += sum(item.stat().st_size for item in group)
        if args.apply:
            for item in group:
                try:
                    item.unlink()
                    removed += 1
                except OSError as exc:
                    print(f"  ! failed to remove {item.name}: {exc}", file=sys.stderr)
        else:
            for item in group:
                print(f"  would remove {item.name}")

    verb = "removed" if args.apply else "would remove"
    print(
        f"\nsnapshots: {len(snapshots)} total, keeping {len(keep)} ({_mib(kept_bytes)}), "
        f"{verb} {len(drop)} ({_mib(dropped_bytes)})"
    )
    if args.apply:
        print(f"files deleted (incl. -shm/-wal): {removed}")
    else:
        print("dry run — pass --apply to delete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
