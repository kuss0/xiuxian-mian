# Bounded Stability Release: 2026-09-16

## Authorization And Scope

The user requested a staged closeout, deployment, commit and push, then asked
to resolve the release prerequisites. This is a bounded release of the existing
candidate, not a restart of the open-ended review goal. C1-C6 residuals remain
in `rebuild-stage-handoff-20260915.md`; deployment does not certify all gameplay.

- Candidate: `/root/xiuxian-main-rebuild-20260907`,
  `rebuild/stability-20260907`, base `cbf152cd` plus staged repairs.
- Prior production: `/opt/xiuxian-main`, `main`, `a41409fd`.
- Intended remote: `xiuxian-mian` (`kuss0/xiuxian-mian`), not wxjerry/origin.
- Preserve World Boss, refinement and other gameplay switches, identity and
  equipment configuration. No manual gameplay test sends. Attempt stays shadow.
- User-owned quiz-bank and `tools/dump_ui_write_keys.py` changes are excluded
  from the release commit and retained in both worktrees.

## Verification

The final isolated suite on September 15 passed 14809 tests and 1376 subtests
(JUnit aggregate 16185), zero failures/errors/skips, 407.345 seconds.
The focused follow-up passed 108 tests; related coverage passed 4419 tests
and 103 subtests. This release reuses those results after verifying the complete
tracked-and-untracked `model/tests` fingerprint:

`7f874cf5921a7b030a71976b95baee0effebb089ed52626c75feff85108a2cec`

No model/test edits are made during release preparation. Configured Ruff,
compilation, dependency and whitespace checks passed at the candidate checkpoint.
Unconfigured full-F finds eight pre-existing unused imports in control.py;
this is not a claim of arbitrary lint profiles passing. Production dependencies
match the pinned requirements, so no package upgrade is needed.

Private evidence/backup root: `/root/xiuxian-release-20260916` (mode 0700).
Final suite evidence is copied there from
`/tmp/xiuxian-c4-rift-wanxin-full-20260915.xml` before deployment.

## Migration Rehearsal

`migration_probe.py` runs in a separate network namespace against a SQLite
backup opened from production read-only, with isolated state/session paths.
It loads, forces a full save, reloads and checks database integrity.
`rehearsal_final.json` records all 24 identities and equality of every original
table's original-column projection. Only `identities.updated_at` is excluded:
normal upsert refreshes it; diagnostic output confirms it is the sole changed
original column. Module switches, timers, runtime state, meta, pending messages,
message indexes, official schedules and all three Attempt tables are identical.
New Yinluo archive tables are expected. This proves snapshot preservation, not
natural-event gameplay correctness or every possible historical migration.

## Deployment And Rollback

1. Commit candidate implementation, tests and documentation; push candidate.
2. Stop only `xiuxian.service`; leave existing observer/watchdog policy intact.
3. Back up the final quiesced database, local settings, sessions and supporting
   state privately. Preserve user-owned files and the prior Git commit/bundle.
4. Repeat the isolated rehearsal on this final backup. If it fails, keep old
   code and restart the old service without changing production state.
5. Fast-forward production main to the candidate, then start the service.
6. Check UI, startup logs, restart counts, persisted configuration and existing
   monitors during a bounded observation window. Push main without force.

Before new runtime dispatch, a coordinated rollback can restore prior code and
its quiesced state snapshot. After any new gameplay or schema/record-format
writes, do NOT restore a stale snapshot or run old readers on the new database.
Stop dispatch, preserve the entire new database and evidence, and reconcile or
forward-fix explicitly. Code-only rollback is unsafe for chat-scoped pending
records, owned operations, accounting journals and Yinluo archives.

Actual deployment hash, timestamps and post-start evidence will be appended
after verification. The 19 subordinate identities' natural cultivation remains
a per-role observation item, not completed solely by passing offline tests.
