# Persistence Delta Lab 2026-07-16

## Boundary

- Branch: `lab/persistence-delta-20260716`
- Base: production main `6a329332`
- Worktree: `/root/xiuxian-main-persistence-lab-20260716`
- Production service, DB, sessions, and runtime control were not modified by this Lab.
- Benchmarks used SQLite backup copies of the live DB under `/tmp`.
- P1 changed business-table persistence only. P2 now changes the last-good
  backup policy in the same Lab branch; neither candidate is deployed.

## Candidate

The candidate preserves the public `save_state()` API and all current call sites.
It adds canonical in-memory snapshots for:

- encoded metadata values;
- identity profile fields;
- module, timer, and runtime columns;
- pending-task rows;
- message-index rows.

Every save still scans all in-memory identities. It writes only metadata keys and
identity rows whose canonical snapshot changed. Unknown snapshot state, a new DB
connection, or a changed DB path falls back to a full write.

Schema repair remains fail-safe:

- the first connection still runs the complete schema migration;
- later saves compare SQLite `schema_version` with the cached version;
- an external `ALTER TABLE` changes the version, triggers a full required-column
  check, and reruns migration when a required column is missing;
- unconditional compatibility migrations no longer execute on every save.

## Consistency Coverage

The Lab tests cover:

- repeated no-change save with zero mutating SQL;
- load/restart initializes snapshots before the next save;
- one identity change writes only that identity;
- metadata-only change does not rewrite identities;
- failed identity write keeps the old snapshot and retries next save;
- connection reopen falls back to full identity persistence;
- identity deletion removes all owned rows and snapshot state;
- empty pending/message state deletes prior child rows;
- metadata and identity changes commit together;
- failure after DB commit but before snapshot refresh causes a conservative rewrite
  on the next save;
- existing runtime-column migration after an external `ALTER TABLE` still works;
- existing identity-collapse, external watchdog pause, and startup recovery tests.

## Benchmark

Live-shaped DB copy at the checkpoint: 24 identities, 14 message-index rows,
about 12 MB.

| Path | No-change time | Mutating SQL | Traced SQL |
|---|---:|---:|---:|
| Current main, backup disabled | 21.45-22.49ms | 225 | 233 |
| Current main, backup enabled | 47.64-48.96ms | 225 | 234 |
| P1 candidate, backup disabled | 8.12-8.98ms | 0 | 2 |
| P1 candidate, backup enabled | 35.70-37.38ms | 0 | 3 |

The two P1 traced statements without backup are one `PRAGMA schema_version` and
one identity-roster `SELECT`. The backup-enabled path adds the roster read used
by the existing last-good guard.

Serialized snapshot size using pickle protocol 5 was about 503 KB:

- metadata: 354,347 bytes;
- identities: 148,887 bytes.

One changed identity still uses the existing full-identity upsert contract:
four parent/state upserts, two child-table deletes, and inserts for any current
pending/message rows. P3 can narrow that later; P1 deliberately does not.

## P2 Guard Backup Generations

P2 removes the unconditional full SQLite backup after every successful
`save_state()`:

- identity roster changes back up immediately;
- account and identity-account mapping changes back up immediately;
- ordinary committed changes back up at most once per configured interval
  (`XIUXIAN_LIVE_GUARD_BACKUP_INTERVAL_SEC`, default 30 minutes);
- a save with no logical change does not copy the database;
- every successful backup is staged and roster-validated before replacement;
- the prior last-good DB is retained as `chaogu_state.previous.db`;
- startup restore uses the previous generation if the newest backup is missing,
  corrupt, or no longer has a live-shaped roster;
- backup failure is caught after the state transaction commits, so it cannot
  turn an already committed state update into a false save failure.

P2 focused coverage includes backup-reason selection, recent ordinary-change
suppression, no-change suppression, account mapping forcing, generation
rotation, backup failure after commit, and fallback restore from the previous
generation.

## Verification

- P2 focused persistence/startup suite: `44 passed`.
- Full suite: `3113 passed, 396 subtests passed in 36.94s`.
- `py_compile`: passed.
- `git diff --check`: passed.

## Remaining Gates

1. Review the snapshot field list against every profile/state column added after
   this Lab base.
2. Add a shadow metric in production that reports would-write identity/meta
   counts without changing save behavior.
3. Observe the shadow distribution for at least 12 hours, including MiniApp
   batches, Telegram reply edits, UI changes, and one restart.
4. Review P2's 30-minute ordinary-change interval against at least 24 hours of
   shadow save frequency before production approval.
5. Do not begin P3 module-level scopes until explicit field ownership replaces
   prefix inference.

This candidate is Lab-green but not approved for production deployment.
