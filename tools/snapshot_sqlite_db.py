#!/usr/bin/env python3
"""Copy a SQLite database into an offline backup staging directory.

The source can be open in WAL mode. The destination must not be opened by
another process: its old journal sidecars are removed before replacement.
"""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import sqlite3
import tempfile
import time


SIDECARS = ("-wal", "-shm", "-journal")


def snapshot_database(source: Path, destination: Path, *, timeout: float = 60.0) -> None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("backup timeout must be finite and positive")
    source = source.resolve(strict=True)
    if destination.is_symlink():
        raise ValueError("backup destination must not be a symlink")
    destination = destination.resolve()
    if source == destination or (
        destination.exists() and source.samefile(destination)
    ):
        raise ValueError("backup destination must differ from source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    os.close(fd)
    temporary = Path(name)
    deadline = time.monotonic() + timeout

    def check_deadline(*_args: int) -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError("SQLite snapshot exceeded backup timeout")

    try:
        reader = sqlite3.connect(
            source.as_uri() + "?mode=ro", uri=True, timeout=min(timeout, 5.0)
        )
        try:
            writer = sqlite3.connect(temporary)
            try:
                reader.backup(writer, pages=256, progress=check_deadline, sleep=0.1)
                writer.execute("PRAGMA journal_mode=DELETE")
                writer.set_progress_handler(
                    lambda: int(time.monotonic() >= deadline), 1000
                )
                result = writer.execute("PRAGMA quick_check").fetchall()
                if result != [("ok",)]:
                    raise RuntimeError("SQLite snapshot quick_check failed")
                check_deadline()
            finally:
                writer.close()
        finally:
            reader.close()
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        # Never combine a new main file with an older staging copy's WAL.
        for suffix in SIDECARS:
            Path(str(destination) + suffix).unlink(missing_ok=True)
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
        for suffix in SIDECARS:
            Path(str(temporary) + suffix).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    snapshot_database(args.source, args.destination, timeout=args.timeout)
    print("SQLite snapshot: quick_check=ok")


if __name__ == "__main__":
    main()
