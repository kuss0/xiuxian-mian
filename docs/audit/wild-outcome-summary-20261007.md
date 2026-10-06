# Wild Outcome Summary

## Evidence And Scope

Lab `/root/xiuxian-wild-outcome-summary-20261007`, base `72d76453`.
Natural ordinary wild results were already queued, but still occupied one
detail per run. The old worker's 01:01:53 summary was confirmed at 1747 UTF-16
units / 23 lines / no explicit mention. Lowering priority did not complete
the user's requested outcome aggregation.

Only the completed, owner-checked MiniApp worker supplies the new optional
metadata. `audit_wild.confirmed_wild_outcome` requires explicit transport,
acted/completed/known flags, a wild-experience action receipt, matching action
and panel counters/timestamps, a one-step counter increment, and an unchanged
server reset boundary. Missing evidence retains the original ordinary row.
Current cultivation deltas and explicit loot quantities are projected, never
cumulative account balances. Tianxing gains reuse `parse_tianxing_text` on the
same action receipt; conflicting numeric evidence is not aggregated.

The logical receipt is stable identity + server reset boundary + run number.
Its full projected payload has a string bucket key. Duplicate rows/counts count
once in the current digest. Contradictory versions remain stored and are
explicitly excluded from the gain total. Positive and negative totals are
displayed separately per resource, so losses cannot disappear through netting.
Identity aliases affect only the latest display name, not receipt ownership.

Original details remain in durable rows. Each identity gets one folded gain
line, subject to the existing summary detail/length budget. Unknown, interrupted,
failed, historical untyped and high-priority/interactive observations keep
their prior routes. There is no historical prose reclassification, game action,
new queue, new retry, scheduler/Tianxing change, or interval change.

## Persistence And Review

`summary_kind` remains empty and no new presentation enum is persisted.
`wild_outcome` and `wild_actor` are optional metadata. Actual `f7958deb`,
`536bbdc9` and `72d76453` validators were executed offline against these rows;
all accepted the old string-key/schema contract unchanged. New readers validate
owner, shape, numeric types and bounds. Standard-library health checks do not
gain game-state imports.

The input is detached before the first queue await. A projection exception
retains the original notification and cannot restart gameplay. Two review
regressions first failed: an older run's later alias was hidden by iteration
order, and a projection exception lost the result notice. Both now pass.

This is aggregation within the existing pending digest, not a durable daily
business ledger. A producer replay after a previous digest has already been
sent is not globally deduplicated. Existing capacity/TTL compaction continues
to report omitted records rather than inventing missing totals. Existing held
delivery policy is unchanged; no replay or queue clearing was performed.

## Validation

- First focused pass: 150 passed; expanded producer/store checks: 290 passed
  / 5 subtests.
- First full candidate: 16661 passed / 1478 subtests, 453.94 seconds.
- Review regressions reproduced 2 failures before repair.
- Post-review broadened validation: 452 passed / 5 subtests.
- Final frozen full suite: 16663 passed / 1478 subtests, 444.38 seconds; JUnit
  `/tmp/xiuxian-wild-outcome-summary-reviewed-20261007.xml`.
- Independent Tianxing/health/notification review: 565 passed / 16 subtests;
  external-quiz notification checks: 27 passed / 8 subtests. Ruff, compileall
  and whitespace checks pass. Ready to merge and push without a restart.

No production loading or natural acceptance is claimed. Worker `f7958deb`
continues to run. WA's October 7 01:11/01:18/01:25 exploration results are
natural evidence of the old Tianxing chain, not acceptance of this notification
candidate. Preserve the loaded-generation distinction after merge/push.
