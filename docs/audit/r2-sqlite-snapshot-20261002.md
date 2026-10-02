# R2 SQLite Snapshot Repair

## Incident

At 2026-10-02 04:22:21 Asia/Shanghai, the existing R2 backup stopped the main
service and its two monitors, then rsync reported that
`data/state/chaogu_state.db-shm` and `chaogu_state.db-wal` had vanished.
Exit code 24 aborted the run before upload. Its EXIT trap restarted the three
services at 04:22:23. This explains that scheduled restart; it is not a worker
crash. October 1's R2 run had completed successfully.

Stopping the application does not make copying SQLite sidecars reliable:
other read-only probes can still open and close WAL connections. Ignoring
all rsync code-24 failures or excluding WAL while copying only the main file
would conceal incomplete backups.

## Repair

- Version the existing operator script at `deploy/xiuxian-r2-backup.sh`.
  The installed entry remains `/root/scripts/xiuxian-r2-backup.sh`.
- Exclude the state DB and its three specific journal sidecars from rsync.
  Other rsync failures still abort; no blanket error suppression was added.
- `tools/snapshot_sqlite_db.py` opens the source read-only and uses SQLite's
  backup API to include committed WAL data. It builds a private temporary
  database, switches it to standalone DELETE journal mode, checks integrity,
  flushes it and replaces the offline staging copy. Old staging journals are
  removed only after the new copy has passed validation.
- Missing/corrupt sources and backup deadlines fail without replacing the old
  staging DB. Source aliases and destination symlinks are rejected. A failed
  run does not upload or prune; the existing service-restart trap is retained.
- Keep the current schedule, retention and stop/start policy. Do not use
  `XIUXIAN_BACKUP_STOP_SERVICES=0` as proof of whole-project consistency:
  Telegram session files and other copied state still require quiescence.
  The helper's destination is an offline staging path, never a live database.

## Validation

- `pytest -q tests/test_snapshot_sqlite_db.py`: 15 passed.
  Includes a real uncheckpointed WAL, paths requiring URI encoding, stale
  staging journals, invalid source/aliases, deadline handling, and three full
  shell runs with mocked systemd/restic and real rsync. No production service
  or cloud API was called by tests.
- Ruff, Python compilation, `bash -n`, and diff checks passed.
- Offline acceptance source:
  `/root/xiuxian-before-native-auto-20261002-0928.db`.
  Output: `/root/xiuxian-r2-acceptance-20261002-9Twrzk/chaogu_state.db`.
  `integrity_check=ok`, 24 identities and 27,396 attempts in both copies,
  standalone journal mode `delete`. No test opened the live database.

## Pending Acceptance

The next scheduled R2 run must produce a new successful restic snapshot.
Installing this fix and verifying a local copy are not cloud-upload acceptance.
Do not clear the failed-unit marker just to make the health display green.
No immediate backup run or game-service restart is required for installation.

Fishing remains on the deployed natural schedule. World Boss and both incense
refinement switches remain off; channel sends stay frozen and Attempt stays
shadow-only. This repair changes no business timers or game runtime code.
