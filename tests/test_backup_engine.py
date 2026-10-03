import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

import pytest

from deploy import backup_engine as engine


def database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE facts(value TEXT)")
        conn.execute("INSERT INTO facts VALUES ('confirmed')")


@pytest.fixture
def config(tmp_path, monkeypatch):
    project = tmp_path / "project"
    database(project / "data/state/chaogu_state.db")
    python = project / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    monkeypatch.setattr(engine.os, "environ", {
        "PROJECT_DIR": str(project), "BACKUP_ROOT": str(tmp_path / "backups"),
        "BACKUP_INSTANCE_ID": "test-instance", "READY_STABLE_SEC": "0.01",
        "RESTIC_REPOSITORY": "/unused", "RESTIC_PASSWORD": "fake",
    })
    monkeypatch.setattr(engine.signal, "signal", lambda *_args: None)
    def deny_commands(*args, **kwargs):
        raise AssertionError(f"Unmocked external command: {args}")
    monkeypatch.setattr(engine, "run", deny_commands)
    cfg = engine.Config("xiuxian")
    cfg.prepare()
    return cfg


@pytest.mark.parametrize("variable,value", [
    ("BACKUP_ROOT", "/root"), ("BACKUP_INSTANCE_ID", ""),
    ("COPY_TIMEOUT_SEC", "nan"), ("COPY_TIMEOUT_SEC", "inf"),
    ("STOP_SERVICES", "perhaps"),
])
def test_invalid_config_rejected_before_commands(config, monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    with pytest.raises(engine.BackupError):
        engine.Config("xiuxian")


def test_source_overlap_and_unowned_target_rejected(config, monkeypatch):
    monkeypatch.setenv("BACKUP_ROOT", str(config.data / "backups"))
    with pytest.raises(engine.BackupError, match="overlaps source"):
        engine.Config("xiuxian")
    config.target.mkdir()
    (config.target / "keep").write_text("old backup")
    with pytest.raises(engine.BackupError, match="not owned"):
        config.prepare()
    assert (config.target / "keep").read_text() == "old backup"


def service_runner(cfg, monkeypatch, *, partial_stop=False, failed_start=False):
    running = {service: service != "xiuxian-listener.service" for service in engine.SERVICES}
    calls = []
    def run(args, *_args, **_kwargs):
        calls.append(args)
        assert args[0] == "systemctl"
        action, name = args[1], args[-1]
        if action == "is-active":
            return (0 if running[name] else 3), ""
        if action == "stop":
            record = json.loads(cfg.recovery.read_text())
            assert name in record["pending"]
            assert record["owner"] == cfg.owner()
            running[name] = False
            if partial_stop:
                raise engine.BackupError("stop timed out after taking effect")
        elif action == "start":
            if failed_start:
                raise engine.BackupError("start failed")
            running[name] = True
        else:
            raise AssertionError(args)
        return 0, ""
    monkeypatch.setattr(engine, "run", run)
    return running, calls


def test_only_previously_active_services_are_recovered(config, monkeypatch):
    running, calls = service_runner(config, monkeypatch)
    apps = engine.Applications(config)
    apps.stop()
    stopped = [args[-1] for args in calls if args[1] == "stop"]
    assert stopped == [s for s in engine.SERVICES if s != "xiuxian-listener.service"]
    restarted = engine.Applications(config)
    restarted.load()
    restarted.resume()
    assert [a[-1] for a in calls if a[1] == "start"] == list(reversed(stopped))
    assert not running["xiuxian-listener.service"]
    assert not config.recovery.exists()


def test_partial_stop_failure_recovers_and_never_publishes(config, monkeypatch):
    running, calls = service_runner(config, monkeypatch, partial_stop=True)
    with pytest.raises(engine.BackupError, match="stop timed out"):
        engine.snapshot(config)
    assert running[engine.SERVICES[0]]
    assert [a[1] for a in calls if a[1] != "is-active"] == ["stop", "start"]
    assert not config.recovery.exists()
    assert not config.target.exists()
    assert not list(config.root.glob(".xiuxian-stage-*"))


def test_failed_recovery_preserves_intent(config, monkeypatch):
    service_runner(config, monkeypatch, failed_start=True)
    apps = engine.Applications(config)
    apps.pending = ["xiuxian.service"]
    apps.save()
    with pytest.raises(engine.BackupError, match="journal retained"):
        apps.resume()
    assert json.loads(config.recovery.read_text())["pending"] == ["xiuxian.service"]


def test_foreign_recovery_journal_cannot_start_services(config):
    engine.atomic_json(config.recovery, {"owner": {"instance": "other"}, "pending": ["xiuxian.service"]})
    with pytest.raises(engine.BackupError, match="another configuration"):
        engine.Applications(config).load()
    engine.atomic_json(config.recovery, {"owner": config.owner(), "pending": ["unrelated.service"]})
    with pytest.raises(engine.BackupError, match="Unexpected service"):
        engine.Applications(config).load()


def test_retention_is_instance_and_path_scoped(config, monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "run", lambda args, *_a, **_kw: calls.append(args))
    engine.retention(config)
    command = calls.pop()
    assert command[command.index("--tag") + 1] == "xiuxian,instance:test-instance"
    assert command[command.index("--path") + 1] == str(config.target)
    assert command[command.index("--group-by") + 1] == "host,paths,tags"
    for period in ("DAILY", "WEEKLY", "MONTHLY"):
        monkeypatch.setenv("RETENTION_" + period, "0")
    with pytest.raises(engine.BackupError, match="positive"):
        engine.retention(config)
    assert calls == []


@pytest.mark.parametrize("failure", ["", "cat", "backup", "check"])
def test_backup_never_prunes_after_failed_upload_or_check(config, monkeypatch, failure):
    calls = []
    def run(args, *_a, **_kw):
        calls.append(args[:2])
        assert args[0] in {"restic", "logger"}
        if args[0] == "restic" and args[1] == failure:
            raise engine.BackupError("injected " + failure)
        return 0, ""
    def snapshot(_cfg):
        calls.append(["snapshot"])
        return config.target
    monkeypatch.setattr(engine, "run", run)
    monkeypatch.setattr(engine, "snapshot", snapshot)
    monkeypatch.setattr(engine.sys, "argv", ["backup_engine.py", "xiuxian", "backup"])
    if failure:
        with pytest.raises(engine.BackupError, match="injected"):
            engine.main()
        assert ["restic", "forget"] not in calls
        assert json.loads(config.status.read_text())["status"] == "failed"
        if failure == "cat":
            assert ["snapshot"] not in calls
    else:
        engine.main()
        assert calls == [["restic", "cat"], ["snapshot"], ["restic", "backup"],
                         ["restic", "check"], ["restic", "forget"]]
        assert json.loads(config.status.read_text())["status"] == "success"


def test_missing_repository_does_not_initialize_without_explicit_flag(config, monkeypatch):
    calls = []
    def run(args, *_a, **_kw):
        calls.append(args)
        return (10 if args[1] == "cat" else 0), ""
    monkeypatch.setattr(engine, "run", run)
    with pytest.raises(engine.BackupError, match="Repository missing"):
        engine.ensure_repo(config)
    assert len(calls) == 1
    monkeypatch.setenv("RESTIC_AUTO_INIT", "1")
    engine.ensure_repo(config)
    assert calls[-1] == ["restic", "init"]


def test_check_config_does_not_recover_or_send(config, monkeypatch):
    apps = engine.Applications(config)
    apps.pending = ["xiuxian.service"]
    apps.save()
    monkeypatch.setattr(engine.sys, "argv", ["backup_engine.py", "xiuxian", "check-config"])
    engine.main()
    assert json.loads(config.recovery.read_text())["pending"] == ["xiuxian.service"]


@pytest.mark.parametrize("requested", ["latest", "abcdef12"])
def test_restore_verifies_into_new_directory_and_selects_instance(config, monkeypatch, requested):
    snapshots = [
        {"id": "abcdef12", "tags": ["xiuxian", config.tag], "time": "2026-10-02", "paths": ["/previous/snapshot"]},
        {"id": "fefefefe", "tags": ["xiuxian", "instance:other"], "time": "2026-10-03", "paths": ["/other/snapshot"]},
    ]
    calls = []
    def run(args, *_a, **_kw):
        calls.append(args)
        if args[:2] == ["restic", "snapshots"]:
            return 0, json.dumps(snapshots)
        assert args[:3] == ["restic", "restore", "abcdef12"]
        assert "--verify" in args
        target = Path(args[args.index("--target") + 1])
        assert target.parent == config.root
        assert target != config.target
        database(target / "previous/snapshot/project/data/state/chaogu_state.db")
        return 0, ""
    monkeypatch.setattr(engine, "run", run)
    engine.restore(config, requested)
    assert len(calls) == 2
    assert not config.target.exists()


def test_snapshot_tree_uses_sqlite_backup_for_sessions_and_summary(tmp_path, monkeypatch):
    if not shutil.which("rsync"):
        pytest.skip("rsync is not installed")
    source, target = tmp_path / "source", tmp_path / "copy"
    source.mkdir()
    name = "session [one] #1.db"
    connection = sqlite3.connect(source / name)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE facts(value TEXT)")
        connection.execute("INSERT INTO facts VALUES ('committed WAL')")
        connection.commit()
        database(source / "data/state/audit_summary.db")
        (source / ".venv").mkdir()
        (source / ".venv/skip").write_text("excluded")
        os.mkfifo(source / "pipe")
        assert not engine.is_sqlite_file(source / "pipe")
        engine.snapshot_tree(source, target, [".venv/"], time.monotonic() + 10)
        for rel, expected in ((name, "committed WAL"), ("data/state/audit_summary.db", "confirmed")):
            with sqlite3.connect((target / rel).as_uri() + "?immutable=1", uri=True) as db:
                assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)
                assert db.execute("PRAGMA journal_mode").fetchone() == ("delete",)
                assert db.execute("SELECT value FROM facts").fetchone() == (expected,)
            assert not Path(str(target / rel) + "-wal").exists()
        assert not (target / ".venv").exists()
    finally:
        connection.close()
