# R130 Treasure Original-Round Recovery

Status: scoped implementation, 70 fault/control cases and final offline
verification complete. Offline candidate only; production remains unvalidated.

## Contract

1. Local accounting and game reconciliation are distinct. Startup/paused
   local recovery sends no HTTP. An authorized public/command run may read
   the current selected player's original round, under existing account
   exclusion, cancellation, entry ownership and request budgets.
2. Reconciliation requires the original hashed session identity. A pending
   enter without a returned session cannot be identified from a fresh board,
   a changed quota or a new day. Other-account, malformed and legacy unbound
   work stays held. Sibling unresolved operations cannot be bypassed.
3. A pending reveal resolves only from same-session selected-cell progress
   or a terminal outcome. A pending settle requires an exact native receipt;
   seeing the old active board never authorizes resending the settlement.
   Known unfinished rounds must retain geometry and revealed cells.
4. Failed/inconclusive reads preserve the original journal and linked result.
   A verified continuation is persisted before any new mutation. It retains
   the prior receipt prefix, but projects only new receipts under a new stable
   operation key. Already applied or retired inventory deltas cannot repeat.
5. Continuation checkpoints, selected identity/account, atomic result commits
   and all save/cancel/restart boundaries obey the same admission rules as
   ordinary work. A failed save cannot dispatch, lose old facts or replace a
   newer owner. A later failure still preserves newly confirmed settlements.
6. Existing UI/background and command callers must actually reach this path;
   passing an unwired helper test does not complete the finding. Disabled
   gameplay does not become HTTP-enabled because local accounting is allowed.

## Required Evidence

- Native public/command flows resume known active, revealed and settled
  original sessions, without replaying enter/reveal/settle mutations.
- Multi-round partial rewards remain exactly once across continuation,
  failed saves, process reload, repeated recovery and inventory retirement.
- Wrong/missing sessions, unchanged reveals, incomplete receipts, account
  replacement, disabled routes and sibling holds cannot authorize a mutation.
- UI/background admission, bounded read attempts, all changed contracts,
  associated modules and the full offline suite pass final review.

## Implementation

- Only an owned, locally accounted operation with an original session key
  can request original-round reconciliation. Pending enters, rebound owners,
  changed MiniApp projections, sibling journals/results and unbound legacy
  unknown snapshots stay held. Local startup recovery remains HTTP-free.
- Public, command and UI/background callers keep their existing permission,
  account exclusion and operation checks. Eligible recovery performs the
  existing selected-player `start` read. A revealed target or exact settlement
  receipt may resolve the original request; inconclusive reads never replay
  it. Full identity loading and both envelope/session aliases are checked.
- New continuation operations use version 2 in the existing journal column.
  They retain the earlier operation/checkpoint identity and a hashed,
  immutable prefix of already accounted receipts. New stable operation keys
  project only receipts after that prefix. Old inventory rows can remain or
  be retired without being recreated or counted again.
- The continuation's verified initial checkpoint must be saved before any
  mutation. Its first admission rechecks the original linked result and
  current MiniApp projection. Failed/inconclusive reads and failed first saves
  preserve the old pair. Later saves, cancellation and local recovery keep
  the existing atomic inventory/state/result contract.
- The old unknown-snapshot guard could reject a proven continuation and
  invalidate its result. Clearing that projection is now limited to prepared
  accounting for the current valid version-2 journal with the exact original
  MiniApp basis. Direct snapshot writes and unlinked results retain the guard.
- An exact original receipt also adopts a round manually completed between
  runs. This is observed game completion, not proof that this process sent the
  old request. No success is inferred from quota, a timer or absent board.
- Failed recovery reads preserve Retry-After through the caller. Background
  recovery retains at least the existing 30-minute unsuccessful-run spacing;
  a longer server wait extends it, but a shorter one cannot reduce it. Both
  identity-loading failures and original-round read failures retain the held
  operation classification. Other gameplay retry policies are unchanged.
- Checkpoint intent admission now rejects an explicitly malformed quota just
  as the worker does. Missing quota alone still permits a verified original
  round to finish; malformed quota cannot authorize reveal or settle requests.
- A full public-entry run may use the existing identity-loader read followed
  by one original-round worker read. Recovery adds no inner read retry loop,
  endpoint or request budget, and local-only recovery still sends no HTTP.

## Verification

- Original native reproducers: **12 failed, 1 passed**;
  `/tmp/xiuxian-r130-repro-20260915.xml`.
- First implementation: **6 failed, 7 passed**; the failures exposed the
  old unknown-projection guard described above. The 13 cases subsequently
  passed. Two earlier R129 tests now allow exactly one authorized read while
  retaining strict no-mutation and unchanged-journal/result assertions.
- Fault tests initially exposed three setup mistakes: targeting the old
  completed result instead of the new commit, replacing a string-key alias
  instead of the actual integer-key identity, and mocking a nonexistent UI
  helper. The tests now use the real keys, commit identity and UI path.
- Six new actual SIGKILL/reload boundaries pass: the continuation snapshot,
  dispatched settle, retained receipt, completed worker, before-DB and after-DB
  accounting. Reload performs no HTTP and preserves previously accounted
  receipts. Combined with the 13 existing interruption boundaries and native
  tests, **58 cases** passed in 29.40s;
  `/tmp/xiuxian-r130-process-first-20260915.xml`.
- The initial **52 new cases** passed in 12.77s, including two-stage interruption,
  retired inventory deltas, failed saves, SQLite reload, real identity loading,
  actual UI/background dispatch and retry spacing, server waits, owner changes,
  ambiguous session echoes and sibling holds;
  `/tmp/xiuxian-r130-controls-reviewed-20260915.xml`.
- The pre-review focused/associated/full suites passed **1009 / 3552 / 13974**
  cases and **19 / 307 / 1275** subtests, respectively;
  `/tmp/xiuxian-r130-{focused,related,full}-reviewed-20260915.xml`.
- Final review found four checkpoint-admission failures: malformed quota
  blocked the worker but not the independent checkpoint consumer. The
  reproducer passed eight valid/missing-quota controls and failed all four
  malformed new/continuation reveal/settle cases;
  `/tmp/xiuxian-r130-admission-repro-20260915.xml`.
  Both layers now reject those intents without another save or mutation.
  Native runs also retain the original round and finish after quota repairs.
- The next review found that a short Retry-After reduced the background
  reconciliation interval from 30 minutes to one minute. The actual
  UI/background path failed for both identity-loader and worker reads;
  `/tmp/xiuxian-r130-loader-spacing-repro-20260915.xml` (**2 failed, 4 passed**).
  Both now preserve the local minimum while honoring longer server waits.
- All **70 new cases** pass in 13.42s;
  `/tmp/xiuxian-r130-controls-accepted-20260915.xml`.
- Final focused suite: **1027 passed, 19 subtests**, 69.56s;
  `/tmp/xiuxian-r130-focused-accepted-20260915.xml`.
- Final associated suite: **3570 passed, 307 subtests**, 113.41s;
  `/tmp/xiuxian-r130-related-accepted-20260915.xml`.
- Final full suite: **13992 passed, 1275 subtests**, 391.04s;
  `/tmp/xiuxian-r130-full-accepted-20260915.xml`.
- Parsed final XML reports contain identical sets of all **70 new cases**,
  with zero failures/errors/skips. All suites used `XIUXIAN_ALLOW_LIVE_TEST_DB=0`
  and an isolated network namespace; only loopback was enabled for local HTTP
  smoke tests. All test sessions ended.
- Configured `ruff check model tests`, scoped full `F` checks, `compileall`,
  `pip check`, tracked and scoped-untracked whitespace checks pass. HEAD remains
  `cbf152cd61325ca9218c74ee995d643038b20905`; inherited quiz/UI-key hashes are
  unchanged. No commit or deployment occurred.

## Rollout Boundary

Only temporary databases have been exercised. A rollout or rollback must
preserve version-2 operation records and their accounted receipt prefixes;
an older reader must not discard them or replay their cumulative receipts.

Historical receipts/identity migration, pending-enter correlation without a
server round ID, general retention/delivery and whole-project Final Review
remain open. R67 production cultivation remains unvalidated. No production
edits/config/DB, game probes, deployment/services/listeners, commit/push or
skill edits; preserve World Boss/refinement switches, inventory API UI-only
use, CommandAttempt shadow-only and Tianxing/deep-retreat independence.
