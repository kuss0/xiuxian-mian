from pathlib import Path
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys

import pytest

from tools.snapshot_sqlite_db import snapshot_database


def make_database(path, *, wal=False):
    connection = sqlite3.connect(path)
    if wal:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE facts(value TEXT)")
    connection.execute("INSERT INTO facts VALUES ('confirmed')")
    connection.commit()
    return connection


def test_snapshot_includes_uncheckpointed_wal_and_is_private(tmp_path):
    source = tmp_path / "source #1.db"
    destination = tmp_path / "backup" / "state.db"
    connection = make_database(source, wal=True)
    try:
        assert Path(str(source) + "-wal").stat().st_size > 0
        snapshot_database(source, destination)
        with sqlite3.connect(destination.as_uri() + "?immutable=1", uri=True) as copied:
            assert copied.execute("SELECT * FROM facts").fetchall() == [("confirmed",)]
            assert copied.execute("PRAGMA quick_check").fetchall() == [("ok",)]
            assert copied.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        assert destination.stat().st_mode & 0o777 == 0o600
        assert connection.execute("SELECT * FROM facts").fetchall() == [("confirmed",)]
    finally:
        connection.close()


def test_snapshot_replaces_old_copy_and_removes_only_its_sidecars(tmp_path):
    source = tmp_path / "live.db"
    destination = tmp_path / "copy.db"
    make_database(source).close()
    destination.write_bytes(b"old database")
    sidecars = [Path(str(destination) + suffix) for suffix in ("-wal", "-shm", "-journal")]
    for sidecar in sidecars:
        sidecar.write_bytes(b"old journal")
    unrelated = tmp_path / "other.db-wal"
    unrelated.write_bytes(b"keep")
    snapshot_database(source, destination)
    assert not any(sidecar.exists() for sidecar in sidecars)
    assert unrelated.read_bytes() == b"keep"
    with sqlite3.connect(destination) as copied:
        assert copied.execute("SELECT * FROM facts").fetchone() == ("confirmed",)


def test_corrupt_source_preserves_previous_snapshot_and_sidecars(tmp_path):
    source = tmp_path / "bad.db"
    source.write_bytes(b"not a sqlite database")
    destination = tmp_path / "copy.db"
    destination.write_bytes(b"keep old snapshot")
    sidecar = Path(str(destination) + "-wal")
    sidecar.write_bytes(b"keep old wal")
    with pytest.raises(sqlite3.DatabaseError):
        snapshot_database(source, destination)
    assert destination.read_bytes() == b"keep old snapshot"
    assert sidecar.read_bytes() == b"keep old wal"
    assert not list(tmp_path.glob(".copy.db.*"))


@pytest.mark.parametrize("alias", ["same", "hardlink", "symlink"])
def test_rejects_source_alias(tmp_path, alias):
    source = tmp_path / "live.db"
    make_database(source).close()
    destination = source if alias == "same" else tmp_path / "alias.db"
    if alias == "hardlink":
        destination.hardlink_to(source)
    elif alias == "symlink":
        destination.symlink_to(source)
    with pytest.raises(ValueError):
        snapshot_database(source, destination)
    with sqlite3.connect(source) as live:
        assert live.execute("SELECT * FROM facts").fetchone() == ("confirmed",)


def test_missing_source_does_not_create_empty_database(tmp_path):
    source = tmp_path / "missing.db"
    destination = tmp_path / "copy.db"
    with pytest.raises(FileNotFoundError):
        snapshot_database(source, destination)
    assert not source.exists()
    assert not destination.exists()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_invalid_timeout(tmp_path, timeout):
    with pytest.raises(ValueError):
        snapshot_database(tmp_path / "live.db", tmp_path / "copy.db", timeout=timeout)


def test_timeout_preserves_previous_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "live.db"
    make_database(source).close()
    destination = tmp_path / "copy.db"
    destination.write_bytes(b"keep")
    now = iter([0, 100])
    monkeypatch.setattr("tools.snapshot_sqlite_db.time.monotonic", lambda: next(now))
    with pytest.raises(TimeoutError):
        snapshot_database(source, destination, timeout=1)
    assert destination.read_bytes() == b"keep"
    assert not list(tmp_path.glob(".copy.db.*"))


@pytest.mark.parametrize("failure", ["", "rsync", "database"])
def test_r2_script_uploads_only_verified_snapshots_and_restores_services(tmp_path, failure):
    rsync = shutil.which("rsync")
    if not rsync:
        pytest.skip("rsync is not installed")
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / "project"
    state = project / "data" / "state"
    state.mkdir(parents=True)
    source = state / "chaogu_state.db"
    connection = None
    if failure == "database":
        source.write_bytes(b"corrupt source")
    else:
        connection = make_database(source, wal=True)
    (project / "tools").mkdir()
    (project / "tools" / "snapshot_sqlite_db.py").symlink_to(root / "tools" / "snapshot_sqlite_db.py")
    (project / ".venv" / "bin").mkdir(parents=True)
    python = project / ".venv" / "bin" / "python"
    python.write_text(
        '#!/bin/sh\nif [ "$1" = "-m" ]; then exit 0; fi\n'
        f'exec {shlex.quote(sys.executable)} "$@"\n'
    )
    python.chmod(0o700)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    scripts = {
        "systemctl": 'printf "systemctl %s\\n" "$*" >> "$CALL_LOG"\n',
        "restic": 'printf "restic %s\\n" "$*" >> "$CALL_LOG"\n',
        "logger": "exit 0\n",
        "cp": "exit 0\n",
        "rsync": (
            "exit 24\n" if failure == "rsync" else f'exec {shlex.quote(rsync)} "$@"\n'
        ),
    }
    for name, body in scripts.items():
        stub = bin_dir / name
        stub.write_text("#!/bin/sh\n" + body)
        stub.chmod(0o700)
    env_file = tmp_path / "fake.env"
    env_file.write_text(
        "export AWS_ACCESS_KEY_ID=fake\nexport AWS_SECRET_ACCESS_KEY=fake\n"
        "export RESTIC_PASSWORD=fake\nexport RESTIC_REPOSITORY=/unused\n"
    )
    call_log = tmp_path / "calls"
    backup_root = tmp_path / "backups"
    snapshot = backup_root / "snapshot"
    backup = snapshot / "project" / "data" / "state" / "chaogu_state.db"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"previous snapshot")
    stale_wal = Path(str(backup) + "-wal")
    stale_wal.write_bytes(b"old WAL must not be replayed")
    (snapshot / ".backup-owner.json").write_text(json.dumps({
        "version": 2, "kind": "xiuxian", "instance": "test-xiuxian-instance",
        "target": str(snapshot), "sources": [str(project)],
    }))
    try:
        result = subprocess.run(
            ["bash", str(root / "deploy" / "xiuxian-r2-backup.sh")],
            cwd=tmp_path,
            env={
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "ENV_FILE": str(env_file),
                "PROJECT_DIR": str(project),
                "SNAPSHOT_DIR": str(snapshot),
                "BACKUP_ROOT": str(backup_root),
                "BACKUP_INSTANCE_ID": "test-xiuxian-instance",
                "READY_STABLE_SEC": "0.01",
                "CALL_LOG": str(call_log),
                "XIUXIAN_BACKUP_STOP_SERVICES": "1",
                "RESTIC_CHECK_AFTER": "0",
            },
            text=True, capture_output=True, timeout=20,
        )
        calls = call_log.read_text().splitlines() if call_log.exists() else []
        stopped = [line for line in calls if line.startswith("systemctl stop ")]
        started = [line for line in calls if line.startswith("systemctl start ")]
        assert len(stopped) == (0 if failure == "database" else 4)
        assert started == [line.replace(" stop ", " start ") for line in reversed(stopped)]
        uploads = [line for line in calls if line.startswith("restic backup ")]
        if failure:
            assert result.returncode != 0
            assert uploads == []
            assert not any(line.startswith("restic forget ") for line in calls)
            assert backup.read_bytes() == b"previous snapshot"
            assert stale_wal.exists()
        else:
            assert result.returncode == 0, result.stdout + result.stderr
            assert len(uploads) == 1
            assert not stale_wal.exists()
            with sqlite3.connect(backup.as_uri() + "?immutable=1", uri=True) as copied:
                assert copied.execute("PRAGMA quick_check").fetchone() == ("ok",)
                assert copied.execute("SELECT * FROM facts").fetchone() == ("confirmed",)
    finally:
        if connection:
            connection.close()
