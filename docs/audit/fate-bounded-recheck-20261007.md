# Fate Quest Recheck

Base: `00803b26`. Isolated Lab: `/root/xiuxian-fate-bounded-recheck-20261007`.
No production restart, manual game request, switch change or state correction.

## Evidence

WA's October 7 quest was still waiting at 24/30 from 00:50:49 after eight
wild actions earned cultivation between 01:11 and 01:51. The fate runner
returned the deep-retreat deadline (up to 12 hours) as its next quest check.
The background scheduler correctly retained that returned wait; it cannot
learn whether the separate quest completed until another authoritative read.

mudamuda0 is a separate issue: saved progress 4, incoming progress 0 at
02:01:08 and 02:31:28. The monotonicity guard is unchanged. No evidence yet
establishes the cause of the upstream discrepancy, and this patch does not
claim to repair it. At 02:45 the two records remain the only unsettled quests.

## Narrow Change

- Successful business waiting uses the existing 30-minute fate recheck limit,
  or the earlier deep-retreat completion window. This is not an early exit or
  settlement command. The normal runner must read the current quest again.
- Unchanged quest progress causes no draw/choose/claim or restart of an active
  retreat. Only an authoritative completed quest permits the existing claim.
- Review found `/start` failure discarded the response's Retry-After when
  raising `MiniAppRequestAborted`. Keep only the existing normalized retry and
  shared-limit metadata on the operation and return it through the same failed
  result. Do not expose the payload, suppress the failure or replay the request.
- No scheduler/state schema change, no clearing the shared retry map, no new
  notification, and no change to request budgets or unknown-action recovery.

The maximum normal business wait is not an execution SLA: server Retry-After,
shared entry holds, higher-priority work and manual pause still take precedence.
This uses periodic authoritative reads, not speculative arithmetic on rewards.
Worst-case recurring uncompleted quests can add an entry/read chain every
30 minutes under existing global admission limits; already settled identities
are still excluded. Observe actual traffic after normal maintenance loading.

## Validation

- Before the patch, seven bounded-wait regressions failed; the two existing
  short-wait cases passed. The first draft of those short-case fixtures assumed
  30 seconds of buffer; corrected to the existing five-second buffer before
  recording the red baseline.
- Two separate `/start` Retry-After regressions failed before metadata retention.
- Broadened fate, cave, scheduler and entry regression: 423 passed / 7 subtests.
  Subsequent meditation-limit, lifecycle and scheduler checks: 188 passed.
- Tests keep transport waits of 1/12 hours, newer concurrent retry deadlines,
  no pre-deadline admission, repeated incomplete reads, one final claim, and
  retained meditation reward after a failed subsequent read.
- Frozen full regression: **16681 passed / 1478 subtests**, 450.64 seconds.
  JUnit: `/tmp/xiuxian-fate-bounded-recheck-20261007.xml`.
- Second maintainer review re-read the wait/failure paths, scheduler ownership
  and before/after-await guards. Its independent test selection covered fate,
  background scheduling, entry routing, Tianxing retreat/timeline, HTTP core
  and notification acceptance: **975 passed / 31 subtests**. This is a separate
  review pass by the maintainer, not an external reviewer.
- Ruff, compileall and diff checks passed. No game-state schema changed.

## Remaining Boundaries

Business waiting and transport waits still share an in-memory scheduling map.
Durable, owner/day/entry-bound waiting and passive gain-triggered invalidation
remain separate debt. The patch neither persists old 12-hour waits nor clears
live waits. Pending/unknown actions, input contracts and date/owner guards are
not relaxed. Cancellation before read-result adoption is not redesigned here.

No natural production acceptance is claimed. Load together with already-tested
staged changes in the next normal maintenance window, then verify the worker
generation, natural fate read/claim and combined daily report. Roll back code
only if needed; do not restore an old game database or replay notifications.
