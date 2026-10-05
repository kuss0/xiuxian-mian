# Fishing Voyage Wait Notification

Base: `4f9b7bb3`. Candidate accepted, not yet deployed.

## Evidence

On 2026-10-06, jfdffdddd at 00:05 and WalterWA2000 at 00:11 received the
explicit native fishing precondition `fishing_companion_sailing`. Their next
fishing times correctly wait for return (03:18:11 and 04:18:23), but the caller
sent a standalone failure notification and the background console said failed.
These waits are not missing replies or unsuccessful fishing casts.

## Change Boundary

Only confirmed `ok=False/status=blocked/fishing_companion_sailing`, with no
committed rod/supply, unknown outcome, pending native rod/supply, cancellation
or ownership/control invalidation, receives the `expected_wait` presentation
marker. A failed state save revokes this classification. The message says to
wait for voyage return, the current last-error display is cleared, and the
existing low-priority summary path is used. The background label says waiting.

Business result remains `ok=False`; no daily completion, terminal skip, quota
increment or success report. No changes to timers, backoff, voyage handoff,
native HTTP, replay, terminal-skip rules or account configuration. Other errors
and uncertain results keep their original normal-priority reporting. Existing
historical state is not rewritten merely to remove an old error label.

## Acceptance And Review

- Before patch: both direct caller and real background integration reproduced
  the missing wait classification/normal-priority notification (2 failures).
- Focused lifecycle and runtime: 251 passed.
- Full suite: **15919 passed, 1438 subtests**, 457.64s.
  `/tmp/xiuxian-fishing-wait-notification-20261006.xml`.
- Separate maintainer second pass: 375 passed, including native journal/supply,
  cancellation, real background ownership and notification acceptance/summary.
  This is not an independent external review.
- Tests preserve known/unknown voyage return clocks, server Retry-After, handoff,
  exact next execution, no extra 30-minute delay, and no daily completion.
  Negative cases cover pending rod, pending supply, save false/exception,
  disabled control, rescheduling, cancellation, unknown outcome and wrong status.
- Ruff, compileall and diff checks pass. No blocker identified in this scope.

## Follow-Up

Deploy in one quiet window after the remaining Lpprceqei wild run, preserving
all switches/timers and the held notification batch. No extra game request or
test notification for acceptance. Natural fishing timing and this wait message
are distinct checks; existing voyages must not be reset to manufacture a wait.

A separate 02:06:46 fate-card anomaly for xianxia9527 is under investigation:
`fate_progress_regressed`, saved progress 5/30. Existing captures retain only
shapes/digests, not the rejected numeric progress. Keep the guard; obtain safe
numeric diagnostics before changing its monotonicity assumption. Do not treat
this unrelated incident as fixed by a notification patch.
