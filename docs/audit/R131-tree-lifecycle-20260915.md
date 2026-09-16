# R131 Tree Native Lifecycle

Status: implemented and verified in the offline candidate. Production remains
unvalidated; this is not whole-project Final Review or rollout approval.

## Required Contract

1. Start, single-game and daily flows use one bounded request budget each.
   Reads, run allocation, submission and reconciliation share it. Mutations
   have no transport retry loop. Existing score profiles and proof algorithms
   are unchanged; a zero score still stops further attempts.
2. Authorization and actual HTTP dispatch respect the current operation across
   awaits, queue waits, proof construction and timing waits. Cancellation stops
   new work and drains an already-dispatched thread before releasing locks.
3. A returned submission and its rewards survive later exceptions, failed
   reconciliation and cancellation. Diagnostic capture/notification failures
   cannot erase accepted work or make callers replay it.
4. An uncertain mutation and a known allocated but unfinished round are
   distinct from a failed read. Retry-After does not authorize another run when
   either is unresolved. A clock/day/entry change is not recovery evidence.
5. Public, direct and command callers retain identity/account and operation
   ownership; a stale result cannot overwrite replacement state or a newer
   coordinator. Confirmed facts may be retained after disable without allowing
   more actions. Public MiniApp and group availability remain independent.
6. Tests exercise actual production adapters and callers with deterministic
   HTTP/auth/proof fixtures, cancellation, control changes and positive cases.
   Passing helper tests alone cannot close an unwired runtime path.

## Implementation

- Start, single-game and daily adapters use a tree-local flow context for one
  shared native request budget, operation checks, returned facts and server
  waits. Actual dispatch still passes through the existing global limiter and
  owner-aware pooled transport. No mutation transport retries were introduced.
- The three production wrappers use the existing draining blocking-flow
  helper. Authorization checks span each RPC await. Local proof construction
  and timing waits are checked before allowing the next request. Received
  results are processed even after cancellation; locks are not released while
  an HTTP thread is still running.
- Daily runs retain accepted scores/materials before parsing later panels or
  proceeding to another round. A known allocation is `open_run`; a dispatched
  uncertain allocation/submission is `outcome_unknown`. Explicit business
  rejection and definitely-unsent cancellation/budget failure remain distinct.
  HTTP 408/425/429/5xx, redirects and malformed responses cannot be downgraded
  to a definite business rejection. Retry-After survives non-retryable HTTP
  classification without granting permission to repeat the mutation.
- A single submit no longer reports the local proof score when the server
  score is absent. Prepared-but-unsubmitted work is not announced as settled.
  Existing scoring/proof algorithms, score ranges, mismatch handling,
  zero-score stops and manual Turnstile handling are unchanged.
- `TreeMiniAppOperation` retains the actual identity/account, tree-record
  basis, authorization generation and coordinator revision. Command, direct,
  public and scheduled paths share its exclusion. New authorizations and
  changed owners/records/coordinators stop subsequent dispatch and cannot be
  overwritten by a late callback. Disable still permits retaining previously
  returned facts for the same owner; it does not permit another action.
- Business state is recorded before optional diagnostics. Capture setup,
  capture delivery, summary formatting and notification failures cannot turn
  returned work into a fresh run. Cancellation during notification carries
  facts only for the original still-current owner. Post-memory save errors
  preserve the in-memory result; this is not crash-safe result accounting.
- The scheduler reserves before queueing and rechecks original owner, enabled
  identity membership, entries and score controls when the task executes.
  Cancellation before the coroutine starts releases only its own reservation.
  The worker reuses the existing public-entry UI's verified-read, shared-limit,
  canary and fallback discipline instead of an independent tree retry loop.
- Unresolved mutations/open allocations remain held across day changes,
  Retry-After expiration and fresh entries. Interrupted `running` records
  cannot be bypassed by direct/public callers, even by reusing the old op ID;
  only the already-reserved original task can use its own running marker.
  The scheduler preserves interrupted facts while marking them unknown.
- Maintenance pause and channel-send freeze do not block a permitted public
  MiniApp operation. The tree scheduler's old unconditional global-enable
  gate now matches the parent MiniApp scheduler's maintenance exception.
  A manual global pause still prevents dispatch.

## Verification

- Original worker reproducer: **40 failed, 1 passed**;
  `/tmp/xiuxian-r131-worker-repro-20260915.xml`. After implementation the
  original 41 cases pass, including six real threaded cancellation boundaries.
- Initial caller reproducer: **69 failed, 5 passed**;
  `/tmp/xiuxian-r131-caller-repro-20260915.xml`. Native direct, command, public
  and queued UI paths now cover owner/control changes, saved facts,
  no-retry classification and eight additional actual HTTP-thread drains.
- Expanded native and worker suites initially passed **150 cases**;
  `/tmp/xiuxian-r131-native-first-20260915.xml`. Existing tests were corrected
  to register real eligible identities and isolate their coordinator/lock.
  Retry-After assertions now use the completion clock instead of enqueue time.
  Budget assertions use the existing `request_count` contract; state
  assertions account for the standard removal of empty dictionaries.
- Final review reproduced **6 failed, 1 passed** for summary failures,
  notification-time owner replacement and standalone entry wait retention;
  `/tmp/xiuxian-r131-final-review-repro-20260915.xml`. All are repaired.
- Maintenance/channel controls reproduced one scheduler-only maintenance
  failure among eight native paths;
  `/tmp/xiuxian-r131-maintenance-repro-20260915.xml`. The manual-pause negative
  control remains enforced.
- The pre-final focused and associated suites passed **515 / 3622 cases**
  and **5 / 295 subtests**, respectively, in **27.54s / 89.37s**;
  `/tmp/xiuxian-r131-focused-20260915.xml` and
  `/tmp/xiuxian-r131-related-20260915.xml`.
- Subsequent review reproduced **four failures** where a persisted running
  marker could be reopened directly, including when the caller supplied the
  same op ID; `/tmp/xiuxian-r131-interrupted-repro-20260915.xml`. The runtime
  now requires the original live reservation, not just a matching string.
- Final focused verification: **519 passed + 5 subtests**, **27.45s**;
  `/tmp/xiuxian-r131-focused-accepted-20260915.xml`.
- Expanded associated verification, including every `test_treasure*.py`:
  **4361 passed + 295 subtests**, **125.00s**;
  `/tmp/xiuxian-r131-related-accepted-20260915.xml`.
- Full isolated verification: **14167 passed + 1275 subtests**, **391.36s**;
  `/tmp/xiuxian-r131-full-accepted-20260915.xml`. Tests use temporary state and
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0` inside `unshare --net`; only namespace loopback
  was enabled for HTTP smoke tests. No external-game network was available.
- Parsed all three final XML reports: no failures/errors/skips, and identical
  sets of all **175 new cases** (59 worker, 116 caller). The JUnit suite totals
  include subtests; the counts above keep them separate.
- Configured Ruff, scoped full-F, compileall, dependency and tracked/untracked
  whitespace checks pass. `model/ui.py` retains exactly seven full-F warnings
  identical to HEAD; that broader check is not green. Inherited quiz-bank and
  dump-tool hashes, and HEAD `cbf152cd61325ca9218c74ee995d643038b20905`, are
  unchanged. All verification sessions have ended. No production evidence is
  implied by these tests.

## Remaining Work

- R132 covers current scalar quota and supplied season/day continuity. Full
  tree receipt authority still needs its own review: strict mode, round/owner
  identity, current field types and chronology, accepted-versus-submitted
  score semantics, and idempotent inventory projection.
- There is no new durable per-request tree journal or original-round recovery
  engine in R131. Forced process death, especially during a manual/direct
  allocation before a result is saved, is not covered by cooperative draining.
  Unknown/open records are held, not automatically reconciled or completed.
- Pre-memory result-commit failure, crash-safe accounting, historical/cross-day
  migration, bounded receipt retention and failed-notification delivery still
  require explicit designs/tests. Do not infer their completion from the
  post-memory save-failure control.
- The seven full-F warnings in `model/ui.py` are identical to those in HEAD;
  they are pre-existing unused imports/locals, not runtime regressions added
  by R131. Configured lint and scoped full-F checks are separate evidence.

## Boundary

No production edits/config/DB, game probes, deploy/restart, listeners,
commit/push, subagents or skill edits. Preserve World Boss/refinement switches,
inventory API UI-only use, CommandAttempt shadow-only and Tianxing/deep-retreat
independence. R67 channel cultivation remains unvalidated in production.

Full native receipt/quota chronology, durable original-round reconstruction,
crash-safe accounting and production acceptance remain whole-project work.
This finding is not full project Final Review or rollout approval. A future
rollout/rollback must preserve unresolved tree markers; an older reader that
ignores them cannot safely resume the affected identities.
