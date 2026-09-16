# R127 Treasure Result Commit And Local Recovery

Status: scoped offline repair, focused/associated/full verification and final
read-through complete. This does not close production acceptance or the
whole-project Final Review.

## Required Contract

1. A returned treasure result must not independently save inventory-delta and
   MiniApp-state projections. Commit the owned projections and their local
   completion marker together, checking literal successful persistence.
2. Failed/throwing writes retain a bounded, secret-free local result for
   recovery, restore only this transaction's projections and never announce
   successful completion or daily exhaustion. Preserve earlier confirmed
   settlements even when the final game outcome is unknown or cancelled.
3. Recovery checks the original identity/account and affected-record bases,
   commits only local facts and performs no game/auth/entry HTTP or Telegram
   command. A retry call must not also start a fresh game operation.
4. Public/command/UI/scheduler admission must honor pending results and active
   workers, including sibling roles sharing the account. Paused/disabled
   gameplay does not authorize sends during local recovery.
5. Corrupt, falsey or oversized persistence records remain explicit holds.
   Ordinary success, late cancellation, notification failure, changed owners,
   SQLite abort/reload, stale records and unrelated writes need verification.

## Implementation

- Added `model/features/treasure_results.py`. Each identity retains one owned,
  versioned `treasure_result`, bounded to 256 KiB. It contains only projected
  business facts, a result summary and affected-row basis hashes, not launch
  URLs, initData, session tokens or raw diagnostic payloads.
- `prepare_inventory_delta` and `prepare_miniapp_state` build records without
  changing memory or saving. Existing recording callers keep their default
  behavior. Treasure commits the prepared inventory delta, MiniApp record and
  `phase=complete` marker in the existing single SQLite transaction; only
  literal `True` acknowledges it. The actual storage bag is not modified.
- Failed/throwing/cancelled commits restore only their changed rows, including
  pruning effects, and retain `phase=pending`. Concurrent unrelated rows,
  metadata, timers and replacement operation markers are preserved. If an
  identity replacement copied this exact uncommitted completion marker, only
  that marker is demoted to pending, not its other state.
- Recovery validates the original identity/account, record shape, material
  consistency and exact affected-row bases. It saves local facts only and
  returns without game/auth/entry HTTP, Telegram commands or notifications.
  Public, command and both UI entry paths recognize these holds before they
  create a new entry. Active account/public workers block recovery as well.
- The background and main-loop paused paths can recover local facts even
  when gameplay is disabled, paused, unconfigured or rate limited. Automatic
  local save attempts are spaced by at least 60 seconds per identity.
  Invalid/conflicting records do not trigger repeated writes or game probes.
- Sibling roles cannot bypass an account's pending or unknown result. An
  unknown outcome remains held after local accounting completes, even if the
  disposable MiniApp summary is removed. A new day or account rebind is not
  recovery evidence. Post-save owner changes do not publish a success.
- Cancellation retains already-returned settlements. Notification/capture
  failures do not repeat accounting. A same-day completed quota receipt also
  stops the scheduler when identical summary content retains yesterday's
  timestamp; recovery of yesterday's result does not exhaust today's quota.

## Verification

- Original native reproducers: **6 failed** before repair, including false,
  None, string and exceptional save acknowledgement, split commits and a
  repeated game call. Artifact: `/tmp/xiuxian-r127-repro-20260915.xml`.
- Initial repair: **259 passed** across result commit, lifecycle and receipt
  suites. Expanded fault testing found one real false rejection caused by
  applying text redaction to serialized JSON. Validation now inspects
  structured string values; redacted errors can be retained safely. Two
  rollback fixture expectations were corrected to change unrelated metadata
  explicitly rather than require retention of this transaction's metadata.
- A subsequent daily-marker reproducer had **1 failed, 1 passed** before the
  scheduler fix: `/tmp/xiuxian-r127-daily-marker-repro-20260915.xml`.
- Added **147 cases** in `tests/test_treasure_result_commit.py`. These cover
  real SQLite abort/reload, both game callers, both UI routes, background and
  paused main loops, literal acknowledgements, cancellation, ownership/basis
  changes, idempotent local recovery, corrupted/oversized records, secret
  handling, account-shared holds, notification failure and daily boundaries.
- Final focus: **407 passed, 10 subtests**, 2.87s;
  `/tmp/xiuxian-r127-focused-verified-20260915.xml`.
- Final expanded associated run: **3235 passed, 307 subtests**, 81.05s;
  `/tmp/xiuxian-r127-related-verified-20260915.xml`.
- Final full regression: **13657 passed, 1275 subtests**, 352.00s;
  `/tmp/xiuxian-r127-full-verified-20260915.xml`.
- All three parsed reports have zero failures/errors/skips and contain the
  exact same 147 new cases. Configured Ruff, scoped F checks, compileall,
  dependency validation and whitespace checks pass. External networking was
  disabled, with only namespace-local loopback enabled for local HTTP
  fixtures. All test sessions have completed.

## Boundaries

Inventory deltas remain pending evidence, not direct storage-bag mutations.
This step does not turn payload digests into historical receipt identity or
migrate old digest keys. Per-dispatch intent durability, process death before
a returned result is persisted, authoritative unknown-action reconciliation,
retention/cross-day receipts and notification delivery remain required work.
No production files/config/DB, live game calls, service/listener operations,
skill changes, deployment, commit/push, World Boss/refinement switches or
CommandAttempt control changes are authorized. R67 subordinate cultivation
still requires production validation.

Rollback must preserve and reconcile the new `treasure_result` column; an old
reader that ignores pending results must not restart treasure. Only temporary
test databases have been migrated. Candidate branch/HEAD remain
`rebuild/stability-20260907` / `cbf152cd61325ca9218c74ee995d643038b20905`.
Inherited quiz-bank and UI-key-tool SHA-256 values remain:

- `89268c5517eaace4deaefd7e47d17cbc5de1162506e993b7a65a6336784f37ec`
- `9e72d7ddceea2e03907d2c45c5dd8b3c9078f5c1dd7fd1e8383b4b0937d42b77`
