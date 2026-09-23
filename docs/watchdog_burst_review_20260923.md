# Watchdog burst review (2026-09-23)

## Verified incident

The first reproduced threshold crossing was at 00:17:24 UTC+8, with eight
script-owned Telegram sends in the preceding 120 seconds across three identities:
three concubine status queries, two divinations, two dream searches, and one heart
trial launch. This first crossing contained no heart-choice command. Subsequent
windows included choices; those were progressing rounds, not blind retries.
MiniApp deep-retreat operations and other players' messages did not enter this
Telegram `sent` count.

The warning was not a fuse. Do not infer either business failure or full system
health from it alone. A current dry-run has no breach, but that does not invalidate
the historical threshold crossing.

## Delivered change

- Build warning details from the same log snapshot, reset boundary, time, and
  exemption selection used for the decision. No second log read for notification.
- Show sample time, window, counted/exempt totals, identity count, top four sources,
  and the most recent five counted command names/message IDs.
- Omit command arguments, escape HTML, and bound displayed fields.
- Flush warning output to the journal immediately, including startup output.
- Keep thresholds, exemptions, five-minute notification throttle, fuse policy,
  send queue, recovery, and business state unchanged.

Validation: 116 watchdog tests passed. Historical replay across 87 real sent
events produced 348 identical old/new count and breach decisions. Test data and
dry-run notification paths do not write production state or notify Telegram.
Only the watchdog needs a restart; the main service does not.

## Remaining work, not silently waived

1. Current heart sessions use UUID operation/session IDs; the watchdog's explicit
   heart-choice burst exemption expects the old round-marked IDs. A future change
   must preserve strict evidence binding and reject duplicate/out-of-order steps.
   A UUID alone is not sufficient evidence for exemption. Fixing this alone would
   not prevent the first threshold crossing described above.
2. Routine multi-identity work can fill the global burst window. Before changing
   pacing, assess time-sensitive follow-up deadlines and existing queue budgets.
   Do not globally exempt concubine sends or raise safety thresholds to hide it.
3. The 08:01 health report still showed three overdue `.双修 温养` pending tasks
   on identity 8574677796. Audit command IDs, replies/edits, and reducer ownership
   separately. No manual DB deletion or automatic resend was performed here.

Rollback is limited to reverting this diagnostic commit and restarting the
watchdog. No live DB migration or business configuration change is involved.
