# R79 Public Tianjige Read-Only Lifecycle

## Scope

Offline candidate only, following R77/R78. The reviewed routes are the
dedicated Tianti status entry and generic Tianjige reads for Tianti, Yinluo and
concubine. No mutation commands, switches, live probes, production restart,
listener activation, skill changes, commit or push are authorized by this work.

Status: scoped implementation, related integration tests and final full-suite
verification pass. This document is not whole-project acceptance.

## Reproduced Failures

- The initial suite produced **58 failures and 6 passes**. Generic Tianti reads
  reported success without updating Tianti. The separate public entry could
  write through deleted/replaced/rebound identities or changed switches,
  schedules and entry leases after an await.
- A cancelled HTTP read could carry a successful transport result although its
  panel was never applied. Conversely, cancellation or an exception in audit
  delivery discarded a business result that had already been saved.
- Tianti treated unrecognized cooldown text as ready, including negative
  wording, accepted conflicting/duplicate fields and invalid progress, and
  could rearm timers while a mutation was pending or reserved. Tianti and
  concubine reads also accepted observations older than their stored snapshot.
- Further review produced **18 failures**: nested session envelopes lost their
  shared-limit flag; local pending work looked like a remote failure to the
  background retry policy; an idle concubine phase could erase unresolved
  command anchors even without a pending-map row.

## Candidate Contract

- The dedicated Tianti entry delegates to the guarded generic read path.
  Generic Tianti now uses its real status bridge and retains the established
  success message and structured sync result. Reading does not enable climb
  automation or send a Telegram command.
- Capture the identity object, account, module state and every associated
  next-action clock. Revalidate the same check before/after session and command
  awaits, inside the HTTP flow and against the parent entry observation.
  Deletion, replacement, rebinding, manual pause, a control/schedule change, or
  newly pending work cannot be overwritten by the older read.
- Reuse Yinluo's existing pending/observation checks before dispatch and on
  synchronization. Tianti checks mutation pending rows and its reserved climb;
  concubine checks active phases, mutation rows and unresolved command anchors.
  Unrelated pending commands do not block reading. Explicit read access still
  works while the corresponding automation switch is off.
- Keep existing channel-send freeze and maintenance exceptions for public
  MiniApps. The new read guards do not turn a chat sending restriction into a
  public-entry outage.
- Cancellation retains the entry lock until the underlying HTTP worker drains.
  A cancelled, unapplied read returns a cancelled workflow result, not raw
  success. After synchronization, audit failure cannot change success into
  failure; audit cancellation carries the actual saved workflow result.
- Shared metadata extraction follows only known result envelopes
  (events/result/extra), including nested and cyclic containers, and accepts a
  typed shared-limit flag. Game payload data is not treated as rate-limit
  metadata. Retry-After and the shared wait survive failure and cancellation.
- A local lock, pending action or active phase reports busy. UI integration
  verifies this stays a local wait without a thirty-minute failure retry,
  entry fallback, or circuit change. Native session limits reach the UI's
  shared wait without trying a second public URL.

## Tianti Panel Policy

- Normalize Markdown, require unique known fields, validate progress/level
  ranges, and accept only explicit positive readiness or a well-formed,
  positive countdown. Repeated duration units, negative/zero/ambiguous waits
  and contradictory Wenxin wording do not authorize actions.
- A recognized locked Gangfeng field is not readiness. An absent Gangfeng
  cooldown does not invent one. Unrecognized negative Gangfeng wording rejects
  the panel rather than matching the substring meaning available.
- A MiniApp status must contain progress, total, cycles, Gangfeng level/total,
  climb cooldown and Wenxin state. Partial panels preserve the entire prior
  state, including progress, errors, freshness and all timers, and report
  incomplete_panel. They do not combine fresh fragments with cached fields
  into an authoritative complete snapshot.
- The old partial-panel test was strengthened to assert this policy, not just
  a different success flag: prior progress remains visible in diagnostics, but
  no field or clock is changed and no fresh/successful synchronization is
  claimed. Both public routes have the same contract.
- The recorded native Tianti panel remains accepted with its actual reported
  remaining wait. Existing shared parser/reducer tests also remain green.

## Verification

- Initial reproducer: **58 failed, 6 passed**;
  /tmp/xiuxian-r79-read-only-before-20260911.xml.
- Expanded related tests before final review: **414 passed, 5 subtests**;
  /tmp/xiuxian-r79-read-only-focused-final-20260911.xml.
- First full isolated suite: **8271 passed, 1275 subtests**, 141.70 seconds;
  /tmp/xiuxian-r79-read-only-full-20260911.xml.
- Review reproducer: **18 failed**;
  /tmp/xiuxian-r79-read-only-review-before-20260911.xml.
- Review fixes and related tests: **444 passed, 5 subtests**;
  /tmp/xiuxian-r79-read-only-review-after-20260911.xml.
- Real UI/background integration: **278 passed**;
  /tmp/xiuxian-r79-read-only-ui-integration-20260911.xml.
- Final full suite: **8303 passed, 1275 subtests**, 141.67 seconds;
  /tmp/xiuxian-r79-read-only-final-full-20260911.xml. This includes 172 new
  read-only lifecycle cases and two added actual UI/background integration
  cases. All runs disable external networking and set
  XIUXIAN_ALLOW_LIVE_TEST_DB=0. Full runs enable loopback for local UI tests.
  Broad Ruff, compileall, pip check and diff checks pass after the review
  changes. All test sessions completed; none was abandoned or restarted.

## Remaining Gates

- Native Tianti scheduling/continuations, receipt chronology, mutation
  accounting, partial group replies and general legacy evidence still need
  their own review. These read-only fixes are not a mutation state machine.
- Concubine's general status reducer, optional-field completeness and native
  awaited lifecycle remain open. Do not infer field-level provenance or full
  workflow correctness from its guarded MiniApp entry.
- General per-game lifecycle, R65/R74 migration/capacity, separately gated R07
  crash durability and whole-project Final Review are not complete.
- R67 production rollout and authoritative channel-retreat baselines remain
  unapproved/unverified. No claim that the subordinate roles resumed
  cultivation follows from these offline tests. World Boss and incense
  refinement remain disabled; CommandAttempt stays shadow-only; deep retreat
  does not block or consume Tianxing effects.
