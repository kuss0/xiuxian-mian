# R2 Engine Source Reconciliation

Base: `89cc99b9`. Lab: `/root/xiuxian-r2-engine-delivery-20261003`.
Scope: record the already-installed Xiuxian backup implementation, preserve the
operator's changes and close its source-delivery gap. No engine behavior changes.
Initial audit and second review are separate passes by the same maintainer.

## Source And Installed Version

The production working tree's uncommitted backup wrapper, engine and updated
snapshot tests were copied unchanged into the Lab. The engine and wrapper match
`/root/scripts` byte-for-byte. The existing restore wrapper is also recorded;
otherwise a clean checkout would still lack the operator's restore entry point.

```text
c85d16a885665f35789a5fa07b37e61b8f5fcbd42410611778c0c2c34b7b33de  deploy/backup_engine.py
81a3c839fcaee3a5fd47f495c82d583c46a65cb2b61c583be1ec57085c0f582e  deploy/xiuxian-r2-backup.sh
1faba53f231be54801367529dd56872938bce8c5ca7e62ea3462f357eb1edabb  deploy/xiuxian-r2-restore.sh
be71cfebf567aefbbe4782cc9ada3350c92feea3a57794cd03ffa5cfd927e1c1  tests/test_snapshot_sqlite_db.py
f6c7c071db5586f13d69d38cdb6573ca6f1eb01755c739a6608a4b01bcace893  tests/test_backup_engine.py
```

`/root/scripts/backup_engine.py` is also used by Vaultwarden and critical-backup
entry points. Do not overwrite that shared installation as a Xiuxian-only
deployment. This review covers its Xiuxian path and shared primitives exercised
by that path, not a new certification of the other applications.

## Initial Audit And Acceptance

Reviewed source/destination ownership and overlap checks, SQLite WAL handling,
intent-before-stop recovery journal, inactive service preservation, failed
upload/check behavior, scoped retention, and isolated restore selection.

Added tests prove:

- Only previously active services restart; inactive listener stays inactive.
- A partially successful stop is recorded before the call and recovered on
  failure. Failed starts retain the journal; foreign service/config journals
  cannot initiate recovery.
- Repository setup failure precedes snapshot/stop. Upload or restic check
  failure never reaches forget/prune. Retention requires a positive policy and
  filters by application, stable instance and snapshot path.
- Missing repository does not silently initialize without the explicit flag.
- Restore selects the correct instance for `latest`, accepts an explicit
  snapshot ID, verifies into a fresh directory and does not replace live state.
- Real temporary SQLite session/summary databases preserve committed WAL data;
  snapshot output passes quick_check and has no copied WAL. Literal rsync
  patterns and nonblocking FIFO inspection are covered.

Focused: **35 passed**. Full isolated suite: **15807 passed, 1430 subtests**,
452.38 seconds. JUnit: `/tmp/xiuxian-r2-engine-delivery-full-20261003.xml`.
Ruff, compilation, shell syntax and diff checks passed.
All tests used `XIUXIAN_ALLOW_LIVE_TEST_DB=0`; subprocess integration uses fake
systemd/restic and temporary source databases. No test stops a live service,
uploads, prunes, restores cloud data or opens the production database.

## Second Review

After full acceptance, re-read recovery stop/start ordering, failure cleanup,
snapshot publication, retention filters and restore-to-live boundaries. Frozen
source hashes remain unchanged. Re-ran backup, snapshot, shutdown and
notification store/health/acceptance tests: **93 passed**.
No additional blocker for recording the installed Xiuxian source baseline.

## Operational Evidence And Limits

- Existing scheduled run: October 3 04:38:16 to 04:40:20 CST, success,
  restic snapshot `a816d136`, repository check reports no errors. It restored
  the main service and the two previously active monitors; listener was not
  started. See `r2-sqlite-snapshot-20261002.md` for the historical incident.
- October 3 21:47:14: installed wrapper `--check-config` succeeded. This reads
  configuration and checks local paths/DB header; it can create the private
  root/lock file, but does not call restic or recover pending services.
- Next scheduled run observed: October 4 04:45:04 CST. Timer and installed
  scripts remain untouched. No extra backup or restart for this review.
- The earlier restic warning about missing HOME/XDG cache did not prevent the
  successful snapshot/check. Cache tuning is not a backup-integrity repair.
- Recovery's readiness check is stable systemd activity, not a business-level
  Telegram health check. The existing main observer supplies the latter.
- Cloud restore was mocked here. A future full disaster-recovery drill and
  audits of the other shared-engine applications are not claimed complete.

## Delivery And Rollback

Merge only the reviewed backup files/tests and this evidence. Preserve the
independent quiz-bank changes. Installed bytes already match, so this is source
delivery only: do not reinstall or restart anything. Keep environment secrets,
instance ID, recovery journals and snapshots out of Git.

Entry points: `bash deploy/xiuxian-r2-backup.sh --check-config`, normal scheduled
backup, `--recover`, and `bash deploy/xiuxian-r2-restore.sh <snapshot-id>`.
Restore creates a separate verified directory; replacing production remains a
separate explicit operation. Existing host systemd units and failure handler
remain host-managed. They are not automatically installed by these files.

There is no runtime rollback for this source-only delivery. A later rollback
of the shared engine needs coordinated review of all its callers and a copy
of the current installation; reverting only this wrapper to its old shell
implementation would discard the reviewed recovery and retention contracts.
