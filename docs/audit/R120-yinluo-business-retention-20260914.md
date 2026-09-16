# R120 Yinluo Business-Point Retention

Status: scoped offline candidate repaired; final focused and full regression
verified. This does not close R65, the whole-project review or deployment gates.
Parent: R65 resource/retention contracts and the whole-project stability audit.

## Reproduction

Two native SQLite/reducer cases use eight hot business slots, then continuously
assist distinct beneficiaries with verified resource coverage. Both stop at
the fifth beneficiary with `hold=capacity`, whether prior beneficiaries remain
registered or are removed. The failed operation has a native financial result
but cannot finish its beneficiary projection. Artifact:
`/tmp/xiuxian-r120-business-repro-20260914.xml` (two failures).

Follow-up fault injection reproduced four integrity gaps:

- Removing a cold row let an old reply acquire a new business clock:
  `/tmp/xiuxian-r120-missing-point-repro-20260914.xml`.
- Omitting the cold insertion after hot removal, or omitting cold removal
  after restoration, still committed incomplete moves. Both failures are in
  `/tmp/xiuxian-r120-staged-move-repro-20260914.xml`.
- A separate SQLite writer removed the original native proof after staging
  but before save; retirement still committed. The single failure is in
  `/tmp/xiuxian-r120-boundaries-first-20260914.xml` (27 other cases passed).
- A shortage reply needs both an assistance clock and a shortage clock. Under
  a reduced five-point bound, reclamation selected the current reply's newly
  staged point, and the no-loss transaction guard rejected the entire terminal
  result. Both action variants failed in
  `/tmp/xiuxian-r120-staged-slot-repro-20260914.xml`.

## Required Contract

1. Distinct current or former assistance beneficiaries must not permanently
   exhaust the provider's bounded hot business-point map. Repeated assistance
   to one beneficiary is not a sufficient long-running capacity test.
2. Retiring a point must preserve its exact identity/account/key and last
   accepted native start/end interval. Late, duplicate or out-of-order replies
   must not regain business authority merely because a hot entry was retired.
3. A retained business clock is neither a financial balance nor a completion
   certificate. Original operation/beneficiary and native receipt ownership
   must remain provable; unresolved spending, foreign ownership and missing
   evidence cannot be discarded to regain capacity.
4. Point retirement/restoration, native resource changes, observations and
   pending cleanup must share the existing SQLite transaction. Stale owners,
   archive revisions, corrupt points and failed writes cannot partially apply.
5. Keep original switches, admission, cooldowns and command transport. No live
   game request, production mutation, new retry controller, CommandAttempt
   control, commit/push or automatic clearing of ambiguous legacy holds.

## Candidate Design

- Only exact `assist:<beneficiary>:banner|strip` points backed by a retained
  native receipt and an originally bound completed assistance operation are
  reclaimable. Fixed action/slot/summary/resource clocks and unproved manual
  legacy points stay hot. No numeric limit is increased.
- A keyed cold business index in the existing SQLite database retains the last
  accepted native interval and its provider identity/account. Its source still
  resolves through the existing hot/cold command archive, including the
  original beneficiary binding. A digest guards insert/remove revisions.
- New replies compare against the retained hot or cold point with the same
  ordering rules. Advancing a cold key restores that key to hot state while
  retiring another provable point if necessary. Duplicate or older input
  cannot erase the cold boundary or obtain a fresh business timestamp.
- All index changes participate in the existing resource state transaction.
  No disk-growth/TTL claim, live migration or release of ambiguous old capacity
  holds follows from this new candidate path.

## Integrity And Transaction

`yinluo_archive_business` stores provider identity/account, the exact business
key, a version-1 point payload and its CAS digest. It does not alter the native
command archive's payload versions or store another balance.

The hot accounting JSON retains `business_archive = {count, digest}`. The
manifest covers all provider-scoped cold keys and payload digests; deleting or
replacing a row is not equivalent to having no previous point. Reading a point
also validates its payload, native command/result evidence and original bound
beneficiary. The manifest scan reads key/digest metadata, not every native
command payload. Its cost is linear in cold business-key count and is not a
claim of unlimited storage or bounded total query cost.

Before committing, every removed hot key must have its exact supported cold
insertion, and a newly hot key with a cold predecessor must have its exact cold
removal. Empty or incomplete change lists cannot discard history or retain
both hot and cold authority. The old interval, staged manifest and live owner
snapshot are checked without type coercion.
Reclamation only selects points unchanged from the pre-update hot snapshot.
It never retires a point inserted or advanced by the current projection. If
fixed/unproved points still leave insufficient space, the terminal fact and
already-accepted business projection commit with the existing capacity hold;
they are not lost to an invalid self-retirement. With another provable old
point available, both business clocks commit without a hold.

SQLite's writer lock is acquired before revalidating native proof and cold
revisions, including source records not themselves being updated. The cold
index, native archive, resource projections, beneficiary observations and
pending cleanup use the existing single save transaction. The resulting
manifest is checked under that lock before commit. Database failure, changed
proof or conflicting writes roll back this update; they do not restore or
delete another writer's records. False returns and exceptions restore the
staged in-memory resource/observation fields as well.

Switches gate new dispatch, not already-confirmed facts. Retirement/restoration
still accepts an owned terminal reply while the provider, beneficiary, module
or global automation is disabled, without enabling controls or sending a new
command.

## Verification

- Final focused suite: **78 passed**, 65.61s,
  `/tmp/xiuxian-r120-focused-final-20260914.xml`.
- The actual default 120-point limit survives 122 distinct beneficiaries and
  beneficiary removal/reload. The run retains 120 hot points and six cold
  points (1836 encoded payload bytes); sha and cultivation remain correct.
  JUnit records these metrics. They are fixture measurements, not production
  estimates or total SQLite/WAL disk usage.
- Reduced-limit native cycles cover retained/deleted beneficiaries, both
  assistance actions, old/new commissions, duplicate/out-of-order/corrected
  replies, contextless edits, hot/cold native proof and restored keys.
- Fault cases cover incomplete staged moves, malformed manifests/payloads,
  missing/replaced rows, missing native proof/index, provider/account changes,
  unbound manual points, writer-lock/CAS races, SQL failures, false/exception
  saves and disabled controls. Temporary SQLite reload is included.
- Associated checkpoint: **2048 passed, 72 subtests passed**, 181.88s,
  `/tmp/xiuxian-r120-related-before-slot-20260914.xml`. The first run found an old
  held-read test replacing the entire staged business map. Both old capacity
  fixtures now seed their full map before staging, preserving the new no-loss
  invariant and the original capacity/atomic-cleanup assertions. The first
  associated artifact is `/tmp/xiuxian-r120-related-first-20260914.xml`.
- That associated checkpoint and the first full run (12808 cases and 1275
  subtests, `/tmp/xiuxian-r120-full-before-slot-20260914.xml`) precede the final
  four-case staged-slot follow-up. They are historical checkpoints, not final
  acceptance of the last guard.
- Final network-isolated full suite: **12812 passed, 1275 subtests passed**,
  326.31s, `/tmp/xiuxian-r120-full-20260914.xml`. It includes all associated
  modules after the last change. Parsed final focused/full reports show no
  failures, errors or skips. Configured/scoped Ruff, dependency consistency,
  focused compilation and whitespace checks pass. All test sessions completed.

## Compatibility And Remaining Gates

This adds a table and a field inside existing accounting JSON. A genuinely old
hot schema is upgraded only with an empty manifest, and the next transaction
must prove the actual provider-scoped cold index is empty. Never synthesize a
missing expected manifest from whatever rows happen to remain in SQLite.
Malformed or mismatched manifests retain the failure instead of resetting
accounting or reopening the old business clock.

Any later deployment must snapshot the hot JSON and all native/business archive
tables together. Old readers do not understand cold business clocks and cannot
safely resume these identities. A code-only rollback, dropping the manifest,
or deleting cold rows is not an approved rollback/reconciliation strategy.
Only temporary test databases were changed here.

Cold storage growth, legacy no-ID recovery, full forced-stop durability, R07,
production channel cultivation and whole-project Final Review remain open.
Unproved manual business points, already-missing facts and ambiguous existing
capacity holds are not silently reclaimed or released. World Boss/refinement
switches, inventory API UI-only use, CommandAttempt shadow-only behavior and
Tianxing/deep-retreat independence are untouched.
