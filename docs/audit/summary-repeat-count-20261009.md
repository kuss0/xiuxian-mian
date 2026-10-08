# Structured Summary Repeat Counts

Base: `5f4288a6`. Worktree:
`/root/xiuxian-summary-repeat-count-20261009`.

## Finding And Scope

At 05:48 a read-only preflush queue snapshot contained a fishing row with
`count=2`. The structured formatter included both observations in the heading
but displayed the body only once without the repeat count. The preview still
contained an expandable quote. This is a presentation loss, not missing game
receipts or a lost queue increment. The preview is not asserted to be the exact
final Telegram payload.

`runtime._format_low_priority_audit_summary` passes the unmodified rows into
`format_grouped_summary`; no outer layer restores the marker. The older format
already displays `xN` beside the last observation time. The narrow change adds
that same convention to structured details. Eight added test cases include
the new large-count parameter for the existing bounded-output test.

The marker counts same-text observations, not independently verified rewards.
Original resource text is never multiplied or rewritten. Latest-per-identity
groups use the selected row's count, not all older records for the identity;
distinct identities retain separate counts. Single observations stay unchanged.
Wild receipts, skip groups, HTML escaping, folding and detail budgets keep their
existing behavior.

There are no changes to queue aggregation, durable schema, priority, flush
cadence, retries, gameplay, cooldowns or flags. No optional trial/voyage Lab is
part of this patch.

## Verification

All pytest commands used `XIUXIAN_ALLOW_LIVE_TEST_DB=0` and isolated state.

- Red baseline: five failures, one passing single-observation control.
- Focused summary/store/wild/message/report tests: 193 passed.
- Second maintainer review: delivery policy, log display, report delivery,
  regressions, health and startup tests: 177 passed / 52 subtests.
- Frozen full suite: 17004 passed / 1511 subtests in 463.68 seconds.
  XML: `/tmp/xiuxian-summary-repeat-count-full-20261009.xml`.
- Ruff, py_compile and `git diff --check` pass.

The durable-reload test enqueues two identical events, reopens the temporary
summary database, checks count 2, then verifies one mocked delivery showing
the marker and unchanged item amount. No test Telegram message or live DB
write was issued. This is a second maintainer review, not an independent audit.

## Loading Boundary

Code acceptance is complete; merge/push this narrow change only. Worker
3981692 remains the process started by the 04:46 scheduled backup recovery.
Do not restart merely to load this display change. Until an existing approved
maintenance reload, the runtime still uses the previous formatter; code on
disk and a successful offline preview are not natural delivery acceptance.

The 05:48:37 ordinary summary was confirmed with receipt
`46cbd8998c9243a29b7d7e0efc31fce9`, 401 UTF-16 units/9 lines/no mentions.
It predates this patch. Queue drained and two historical held batches stayed
untouched. Lpprceqei's 05:50:02 fate read still reports 0 against old accepted 1;
its next ordinary check is not before 06:20:02. WA's 07:19:55 rift is unchanged.
Foreground monitor 18303 remains active; do not end monitoring at this commit.

At 06:06 `52d8fcc2` was fast-forwarded to main and pushed to `xiuxian-mian/main`.
Production-directory isolated recheck passed 102 tests. Worker 3981692 and
supervisor 3981690 are unchanged; this formatter is still pending maintenance
loading. The only unrelated production dirty file remains the learned quiz bank.
