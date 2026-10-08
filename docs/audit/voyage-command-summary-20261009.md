# Command Voyage Summary

Base: `af0b1124`. Scope: one notification priority, no business changes.

## Evidence

WA sent one `.远航归来` at 00:07:26 on October 9, 2026:
`-1002083016447 / 1304833`, official reply `1304836` at 00:07:27.
The accepted result contains cultivation +357, stones +143, one soulwood
and affinity +9. Receipt `108424d12c294818a0a063a13cb4785e` confirms a
standalone normal notification, 94 UTF-16 units / two lines. Do not replay it.

The native return already uses the durable low-priority summary. The command
helper still explicitly selected medium priority and bypassed batching.

## Change And Review

Use low priority for the already accepted command-return reward notice.
Keep the formatter, identity, display budget, reward text, negative affinity,
reply ownership/deduplication, action completion, errors and game transport.
No new queue, retry, schema, flag, timer or state correction.

All pytest runs used `XIUXIAN_ALLOW_LIVE_TEST_DB=0` and isolated state:

- Red baseline: both new durable-summary integration cases failed because
  the original helper delivered immediately.
- Focused voyage/affinity/native regression: 520 passed.
- Notification/companion/fishing regression: 3387 passed.
- Second maintainer review: 509 passed, covering public callers, owned
  actions, fishing handoff, summary persistence and notification acceptance.
  This is a second review pass, not an independent external audit.
- Ruff, compilation and diff checks pass.
- Before planned loading, the frozen full suite passed: 16975 tests /
  1486 subtests, 439.39 seconds; XML
  `/tmp/xiuxian-voyage-command-summary-full-20261009.xml`.

The new test routes an owned official reply through the real handler into a
temporary durable summary store. Repeated replies leave one row; a later
flush delivers one folded, unmentioned notice with reward/loss details.
Unknown delivery remains held without automatic replay or game-command send.
Existing audit-failure tests keep completed business state irreversible.

## Loading And Observation

Merge/push after review; defer loading until a quiet maintenance window.
This document does not claim the new command notice has been observed live.
The current worker remains `3816200`, loaded through `f005d86d` at 22:13:35
on October 8. Do not restart solely for this notification adjustment.

At 00:23, main/observer/watchdog are active, NRestarts zero. No new runtime
error, stuck reply or watchdog failure. Two historical held notification
batches remain untouched. Quiz-bank working changes are unrelated.

WA naturally caught three fish and resumed sailing after the existing
15-minute return-to-fishing window; its future general companion timer did
not prevent eligibility-based dispatch. Remaining casts await the next
return. No timer or configuration was changed to force this observation.

Continue watching natural WA wild preparation at 01:12:14, wild due 01:22:14,
and rift preparation around 07:09:55 / due 07:19:55. Foreground follower
`10441` must be renewed before approximately 05:35. Frozen-channel voyage
coverage, the separate unmerged voyage-durability Lab, partial faith-change
attribution and held-notification review remain separate debts.
