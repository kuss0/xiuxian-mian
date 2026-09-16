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

## Executed Release

- Implementation release: `e0e210943992d28ea2ecb787fe63285133af8a2c`.
  Both production main and the rebuild branch were pushed to `xiuxian-mian`;
  `ls-remote` confirmed both refs. A later documentation-only commit records
  this outcome and requires no further runtime restart.
- Main service stopped cleanly at 09:04:29 UTC+8 and restarted at 09:05:52.
  Worker reported successful state restoration and startup at 09:06:12.
- Final backup: `predeploy.db`, `predeploy-local-state.tar` and
  `predeploy-source.bundle` under the private release root. Bundle verification
  passed and includes both the prior main and candidate histories. The archive
  contains local environment, sessions, supporting state and both user-owned
  files. It excludes monitor output directories and the old backup directory.
  Tar reported the state directory mtime changing while independent observers
  remained active; required archive members were verified. The dedicated
  SQLite backup is the authoritative consistent database snapshot, not the
  changing directory metadata or observer state in the archive.
- `quiesced_final.json` records a second successful migration rehearsal on the
  final stopped-service backup, with all original-table invariants passing.
- `postdeploy_observation.json` contains five read-only checks from 09:08:31
  through 09:10:33. Main PID stayed 1777563, `NRestarts=0`, UI HTTP 200,
  SQLite quick_check OK, 24 identities retained, enabled identities and global
  enable unchanged, and module settings unchanged. Daily completion facts are
  excluded from live configuration comparisons; `last_tower_day` legitimately
  changed from yesterday to empty during normal window initialization.
- World Boss enabled count remains 0; small-world refinement enabled count
  remains 0. Quiz and personal tool hashes match the handoff in both worktrees.
- Health observer and soft watchdog stayed active without restarts; listener
  remains inactive. Health score was 100, but it is not gameplay acceptance.
  Existing service environment still enables Attempt shadow write/bind only;
  report-only recovery and all control module/identity flags remain disabled.
- Dynamic public entrance collection and natural treasure daily-limit handling
  were observed. No manual gameplay probes, inventory reads or forced retries
  were issued by the release procedure.

## Explicit Residuals At Closeout

1. `myios17` fate-cards reported `fate_read_failed` at 09:06:27. Existing local
   capture shows HTTP 200 and parsed JSON at the start endpoint; this is not
   proof of a dead entrance. Later result validation/lifecycle needs a bounded
   follow-up with the retained evidence, not blind retry or reopening everything.
2. One `.我的阴罗幡` query sent naturally at 09:06:24 remained pending during
   the observation window, without duplicate sends. No success is claimed and
   no cooldown/pending evidence was cleared to make the report green.
3. Nineteen channel identities retain their existing group-send freeze. Their
   per-role MiniApp cultivation is not proven by this deployment.
4. Remote CI status could not be read because `gh` is not authenticated. Local
   full-suite evidence and Git push/ref verification are complete; remote CI
   success is not claimed.

The open-ended goal remains non-running (`blocked` in the available goal API),
not falsely marked whole-project complete. This release ends the foreground
work; the existing health/watchdog services continue independently.
