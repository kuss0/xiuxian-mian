# R96 Concubine External Observations

Date: 2026-09-12.
Candidate: /root/xiuxian-main-rebuild-20260907.
Branch: rebuild/stability-20260907.
HEAD: cbf152cd61325ca9218c74ee995d643038b20905, unchanged.

Offline candidate only. No production files, services, settings, database,
game requests, listeners, skills, commits, pushes or deployments are changed.
World Boss/refinement remain disabled; inventory API remains explicit UI-only;
CommandAttempt remains shadow-only. Deep retreat never consumes or blocks
Tianxing effects. Whole-project Final Review remains open.

## Finding And Reproduction

External contracts, affinity gains/depletion and partner-loss handlers could
directly replace partner state, invent/reset cooldowns, repeat affinity deltas
and clear active operations. Their in-memory broadcast claims preceded durable
saves. A text mention could also substitute for the actual contract owner.

- Initial R96 reproducer: 22 failed; first implementation: 22 passed.
  /tmp/xiuxian-r96-external-reproduced-20260912.xml.
  /tmp/xiuxian-r96-external-first-20260912.xml.
- Expanded calibration review: 6 failed, 50 passed. Empty malformed contexts
  were accepted, pending new-partner events still used the cached old name,
  and an unsent ready puzzle prevented calibration.
  /tmp/xiuxian-r96-external-expanded-first-20260912.xml.
- Evidence/queue review: 10 failed, 229 passed. Conflicting event clocks/types,
  invalid or conflicting original-message IDs and boolean numeric facts were
  accepted. Scoped queue/reply cases otherwise passed.
  /tmp/xiuxian-r96-external-trust-first-20260912.xml.
- Final review: 6 failed, 114 passed. Missing absolute affinity and absence
  contradicting a permanent partner cleared the observation; broadcast loops
  could enter another identity deleted during the owner's notification.
  /tmp/xiuxian-r96-final-review-first-20260912.xml.
- Clock/replacement review: 2 failed. Calibration dispatched at the event's
  timestamp even though such a read cannot reconcile it; a new partner inherited
  the old partner's future cooldowns instead of the new absolute panel.
  /tmp/xiuxian-r96-clock-partner-first-20260912.xml.

These are offline reproductions, not production incident counts. Seven former
affinity tests assumed ID-only text could directly mutate business state. They
now use official sender/chat/time evidence and assert pending calibration;
positive native/MiniApp calibration is covered separately. A mistakenly removed
shared broadcast iterator was caught by Ruff and restored before full testing.
One broad test invocation named a nonexistent test file and ran no cases; it
was corrected and does not count as verification.

## Observation Contract

- One bounded concubine_external_observation JSON record per identity, registered
  in runtime/default/JSON fields and both SQLite declarations. It stores the
  latest qualified event, pending/complete state and calibration retry/resolution
  clocks, not an additive affinity ledger or a mutation controller.
- Bounded parsers distinguish contract, gain, selfless depletion and loss.
  Require an official bot, configured game chat, original server event/edit
  timestamp, typed IDs/numbers and no forwarded source. Reconcile any supplied
  parent/context metadata with the actual event; do not coerce strings/booleans
  or use local receipt time as server evidence.
- Explicit contract owner and unique current/alias username determine ownership.
  Unmentioned gain/depletion requires an explicit routed identity and owned
  original parent in the same chat. Ambiguous aliases, wrong partners and
  permanent-partner loss cannot invalidate another role.
- Events received while paused remain observations only. Saving them does not
  reset affinity, availability, cooldowns, phases, pending tasks or the existing
  schedule. Save failure rolls back the marker; no early broadcast claim prevents
  re-delivery. Successful save survives notification failure/cancellation.
- Pending events coalesce behind the latest server timestamp without restarting
  the retry deadline. Matching follows the pending replacement partner, not
  only the older cached snapshot. Older events covered by an absolute snapshot
  or completed barrier cannot reapply a delta.
- New business actions are blocked while observation is pending or malformed.
  Queued mutations recheck this condition. The marker is excluded from the
  business plan fingerprint so a definitely-unsent operation can release its
  own unchanged phase; unknown sends and owned results retain their lifecycle.
  An owned heart prompt can finish its original choices/read probes, but no new
  heart launch is admitted from stale partner state.

## Calibration And Completion

- Existing owned recovery runs before external calibration. The outer due scan
  also considers the observation deadline, even if the old business timer lies
  far in the future. Outer failure diagnostics do not replace the owned plan.
- Persist a 60-second minimum calibration-attempt interval before dispatch.
  Wait until the local dispatch clock is later than the server event clock.
  Repeated notifications cannot accelerate retries; unknown read recovery uses
  the existing owned query lifecycle rather than a second retry engine.
- A native status query must have dispatched after the latest observation.
  A completed read started earlier may close its own pending record but cannot
  clear the observation or apply a stale snapshot.
- A fresh identity-validated MiniApp panel can also reconcile it. The existing
  public-entry business snapshot includes this JSON marker, so changes during
  session loading or HTTP execution invalidate the returned panel.
- Positive calibration requires an explicit absolute affinity as well as the
  normal complete panel. Red-dust omission is not permission to reuse cached
  affinity after an external change. Missing fields, manual-repair text or
  absence contradicting a permanent partner leave calibration unresolved.
- The idle/puzzle-ready read can proceed only when no owned or legacy mutation
  blocks it. An unsent ready puzzle must not cause a calibration deadlock.
- Apply snapshot and observation completion in one checked save. Rollback keeps
  original query/pending/observation state. A confirmed permanent moon partner
  disables automatic reacquisition. A replacement partner adopts its own panel
  cooldowns; same-partner future-cooldown merge policy is unchanged.
- Native external affinity text is not terminal evidence for its parent command.
  New/edit loss fallbacks share one dispatcher; all external broadcasts stop at
  their unique owner without continuing across notification awaits.
- Remove obsolete direct affinity/partner mutation helpers and unused loss/
  contract regexes. Keep shared gift/greeting and selfless parser dependencies.
  UI status text distinguishes pending calibration from malformed observation.

## Verification

- 122 observation cases, 26 cross-action queue/completion cases, 2 heart cases
  and 3 MiniApp worker cases. Includes native routing, disabled broadcasts,
  aliases/rebinding, original/edit clocks, bounded fields, cancellation, SQLite
  reload, transactional save failure, queue races, absolute calibration and
  absence/missing-field conflicts.
- Final focused: 3694 passed, 183 subtests, 26.57s, exit code 0.
  /tmp/xiuxian-r96-focused-final-20260912.xml.
- Configured and fatal Ruff, compileall, pip check and git diff --check pass.
- Final isolated full suite: 10946 passed, 1275 subtests, 164.28s, exit code 0.
  /tmp/xiuxian-r96-full-first-20260912.xml.
  JUnit records 12221 cases including subtests, zero failures/errors/skips.
  Tests use temporary SQLite with XIUXIAN_ALLOW_LIVE_TEST_DB=0 and a network
  namespace with only loopback enabled. All test sessions completed. Only
  documentation changed after this full-suite run; production reconciliation
  and whole-project acceptance remain unproven.

## Remaining Gates

This is not a complete external-event history or a repair of every manual and
legacy concubine path. Missing original server evidence cannot be invented.
If a live red-dust panel does not expose absolute affinity, that observation
must stay unresolved until an authoritative compatible panel is available;
do not clear it or resume spending merely to make the scheduler look healthy.
The shared forced-stop/save durability and historical evidence-availability
limits remain separate gates, not guarantees supplied by this marker.

R65/R74 accounting/capacity, legacy migration, shared R07 durability, per-game
integration, rollout/rollback and whole-project Final Review remain open.
The new marker must survive migration/rollback; an older reader that ignores
it must not resume stale business decisions or the former direct-delta code.

R67's production jfdffdddd subordinate-role MiniApp cultivation is still not
revalidated or resumed by this work. Candidate selected-player fixes need a
separately authorized rollout and identity-bound production baselines.
