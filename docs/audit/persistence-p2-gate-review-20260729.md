# Persistence P2 Gate Review (2026-07-29)

## Verdict

- Lab implementation: **green**.
- Production deployment: **approved and completed after mainline review**.
- The old `fbecbe91` candidate was not cherry-picked. It changes the default
  guard-backup cadence from the production six-hour policy to 30 minutes and
  restores over the state DB without first removing stale WAL/SHM sidecars.

This branch rebases the P2 design on current `main` after P1 delta persistence.
It keeps the existing six-hour ordinary-change cadence, adds immediate backups
for roster/account-map changes, and adds a validated previous generation.

## Scope

Changed only the live-guard backup and restore path in `model/persistence.py`:

- no-change saves do not create a full DB backup;
- ordinary changed saves refresh at the existing six-hour interval;
- identity roster and account-map changes request an immediate backup;
- a staged SQLite backup must pass `PRAGMA quick_check` and exact roster
  comparison before it can replace the current generation;
- the former current generation is retained as `chaogu_state.previous.db`;
- backup failure does not roll back an already committed state transaction;
- restore validates current then previous generation and falls back to previous
  when current is corrupt;
- restore archives the suspicious main DB and its sidecars, validates a staged
  copy, removes stale target WAL/SHM/journal files, then atomically replaces the
  main DB;
- missing DB, valid first-run DB, corrupt DB, and valid empty identity table are
  distinguished instead of all collapsing to an empty roster result.

No scheduler, Telegram send path, MiniApp path, reducer, cooldown, UI setting,
or runtime control flag is changed.

## Cadence Decision

Production currently uses a six-hour live-guard refresh. The 24-hour shadow
report proves that a 30-minute candidate is affordable, but affordability is
not sufficient reason to shorten the recovery history. With two generations,
six hours preserves a materially wider rollback window while roster/account
structure changes still receive immediate snapshots.

The preferred environment key is now
`XIUXIAN_LIVE_GUARD_BACKUP_INTERVAL_SEC`; the legacy
`XIUXIAN_LIVE_GUARD_REFRESH_SEC` remains a fallback. The default remains six
hours and the minimum remains five minutes. Shadow telemetry uses the same
resolution order and cadence after this gate, so its backup-reason counts do
not continue reporting the superseded 30-minute candidate policy.

## Validation

Focused persistence and startup recovery suite:

```text
118 passed, 50 subtests passed
```

Full repository suite:

```text
3396 passed, 535 subtests passed
```

P2-specific adversarial coverage includes:

- no-change and fresh ordinary-change backup suppression;
- immediate account-map backup reason;
- current/previous generation rotation;
- backup failure after commit;
- replacement failure restoring the former current generation;
- corrupt current guard falling back to previous;
- stale main DB WAL/SHM archival and removal before restore;
- both guard generations failing `quick_check`;
- corrupt main DB restoration;
- missing main DB not restoring a stale guard automatically.

Static checks:

```text
python -m py_compile model/persistence.py tests/test_persistence_guard_generations.py
python -m compileall -q model tests
git diff --check
```

Both passed.

## Live-Shaped Read-Only Benchmark

Input was a temporary SQLite backup of the production DB; production was not
written. Snapshot size: `33,230,848` bytes, 24 identities.

```text
first generation:   74.94 ms
second generation: 117.68 ms
previous fallback restore: 161.87 ms
```

Both generations passed `quick_check`, retained all 24 identities, and left no
guard WAL/SHM files. The restore rehearsal selected a deliberately preserved
previous generation after corrupting current and recovered all 24 identities.

## Production Gate Result

The mainline controller completed the production gate on 2026-07-29:

1. Replayed the Lab commit onto current mainline `8314c60b`; focused persistence
   tests passed (`118 passed, 50 subtests passed`) and the exact merge candidate
   passed the full suite (`3400 passed, 535 subtests passed`).
2. `compileall` and `git diff --check` passed. The production DB and existing
   guard both passed `PRAGMA quick_check` with 24 identities and zero pending
   tasks.
3. Created `data/state/chaogu_state.db.pre-persistence-p2-20260729073703` and
   `/root/xiuxian-main-live-guard/manifest.pre-persistence-p2-20260729073703.json`.
4. Deployed commit `919352cb` in a low-activity window and restarted only
   `xiuxian.service`.
5. Startup restored 24 identities successfully; pending remained zero;
   health/watchdog/preflight were normal apart from the known frozen channel
   send-as cohort.
6. The startup no-change save did not rewrite the guard: the current DB and
   manifest retained their `2026-07-29 02:24:18` timestamps.

The first natural six-hour generation remains an observation checkpoint. A
previous-generation restore continues to be rehearsed only against copied Lab
databases; production corruption must not be induced for validation.
