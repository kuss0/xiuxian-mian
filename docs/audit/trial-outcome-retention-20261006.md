# Completed Trial Outcome Retention

Base: `2ed3aa2d`. Worktree: `/root/xiuxian-trial-outcome-retention-20261006`.
Merged to disk and pushed as `a302c172`; production worker remains on
`f7958deb`. Post-merge isolated verification: 170 passed / two subtests.
No restart, gameplay action or state rewrite was performed for this change.

## Scope

`_persist_trial_daily_batch_state(completed=True)` previously erased the
`last_outcomes` it had just received, before the completion notification.
The existing batch config could prove completion, but not its reported gains.
The normalizer, persistence helper and running batch also shared nested outcome
dictionaries with their callers, allowing unsaved in-place snapshot changes.

Keep the supplied completed outcomes in the existing per-wave field. Copy
nested dictionaries at read, write and run boundaries. No new schema, queue,
sender, status, counter, retry or gameplay action. Empty completion stores an
empty snapshot. The next running wave still explicitly initializes its outcomes,
so storage stays bounded to the two existing latest-wave snapshots.

Completion still clears execution counters/steps, stamps the day and writes
before notifying. A retained outcome is not a delivery acknowledgment or a
reason to reopen work. False/None/error/cancellation from the final notifier
leave completion and its snapshot intact; no notification replay is added.
Existing same-day and wave guards still decide execution eligibility.

## Verification

Before the patch: nine new regressions failed, three passed. Failures reproduce
the erased completion and nested aliasing rather than a missing test fixture.
After the patch: focused outcome, entry, trial batch and checkpoint tests pass:
172 tests, two subtests. Frozen full suite: **16045 passed / 1461 subtests**,
461.18s. JUnit: `/tmp/xiuxian-trial-outcome-retention-full-20261006.xml`.
Second maintainer review: **326 passed**, covering checkpoint ownership,
process restart, UI lifecycle and summary delivery. This is a second pass by
the same maintainer, not an independent external audit. Ruff, compilation
and `git diff --check` pass. All `last_outcomes` readers were reviewed: only
the same-day retry helper feeds them back into execution, and it rejects
completed status. No blocker found for this narrow retention change.

New coverage includes serialization/reload, preservation before all five
delivery outcomes, no-work completion, separate waves, cross-day reset,
completed-state non-resumption, independent snapshots, resumed aggregation,
and unchanged failed-step hold. Tests run with `XIUXIAN_ALLOW_LIVE_TEST_DB=0`.

## Remaining Debt

This does not close the complete child/batch duplication issue. Failed-step
retry still replaces its aggregate, and cancellation before the final checkpoint
still needs a durable per-operation reward handoff. A returned native result can
contain cumulative receipts; blindly carrying totals into a retry can double
count. Individual outcome notifications remain until that boundary is tested.
No old production snapshot is reconstructed or claimed to contain these gains.

No main restart is justified for this observability-only change. After review,
merge and push the candidate, then wait for normal maintenance to load it.
Natural final-wave retention acceptance remains pending until that happens.

## Concurrent Live Observation

Three due channel YuanYing cycles naturally completed status-first launch:
`growrdick` 08:12:45, `imcanonical_ai` 08:18:28 and `xueuode5` 08:22:38.
Captures bind the respective negative player IDs; each has one new launch and
a future 16:12/16:18/16:22 deadline, no pending probe. Baji naturally harvested
5787 incense at 08:21:06; next harvest is 16:21:06. Both WA/Baji automatic
refining switches remain off. No gameplay was triggered for verification.
