# October 9 Live Notification And Trial Lab Checkpoint

Evidence window: 01:12-01:55 CST. This is a checkpoint, not completion of the
continuous monitoring request or of all notification debts.

## Runtime Boundaries

Production HEAD before this documentation commit: `9666fa1a`. Worker `3816200`
still loads `f005d86d` from October 8 22:13:35. Supervisor `3816199`, observer
`3435134`, watchdog `3435106` are active with NRestarts=0. The independent
listener remains intentionally inactive; the main listener handles both groups.
Foreground monitor `10441` remains active, due to expire around 05:35; renew it
before expiry for the 07:19 rift window. No restart, game probe, live database
mutation, switch change or test Telegram notification occurred in this window.
The unrelated learned quiz-bank modification remains untouched.

`638955a6` command-voyage notification grouping is pushed but not loaded.
The manual native fishing report is usable without restarting the worker.
WA/Baji duel and incense refinement remain disabled.

## WA Natural Wild Acceptance

- Eight HTTP journey actions, all single-attempt, from 01:15:19 through
  01:38:57; stored server result has dailyCount=8, dailyLimit=8,
  dailyRemaining=0, available=false. Next scheduled run: October 10 01:29:13.
- All eight script predictions have strictly anchored official replies:
  `1305253 -> 1305256`, `1305284 -> 1305285`, `1305312 -> 1305313`,
  `1305327 -> 1305328`, `1305353 -> 1305354`, `1305355 -> 1305356`,
  `1305360 -> 1305361`, `1305366 -> 1305367` in group `-1002083016447`.
- First-round retained change protection was confirmed on October 8 by
  `1297775 -> 1297776`, valid for 24 hours. The first two rounds triggered
  change protection. Fresh replacements were confirmed by
  `1305287 -> 1305289` and `1305314 -> 1305315` before subsequent runs.
- Last server result explicitly contains prediction hit and change remaining
  23h39m. Local prediction is consumed; change remains. The apparent 20-minute
  timer while a worker is active is its run lease, not a newly imposed CD.
- Eight results: two protected escapes, then six cultivation gains of 45000.
  Journal material lines total stones 3974, soul-nourishing wood 2 and
  cold-condensed crystal 1. This is live evidence, not a new reward write or
  a replay notification.

Rift due remains 07:19:55. Preparation is expected around 07:09:55. This wild
acceptance does not replace natural acceptance of the separate rift-CD fix.

## Notification Delivery

- 01:13:43 trial batch notice confirmed: receipt
  `5a30eb52fef84fc0a24096893603a7d7`, 119 UTF-16 units, three lines, no mentions.
  Natural batch completed 12/12, 36 settlements, Tianji traces +516.
- 01:32:25 routine summary confirmed: receipt
  `6a01fef0ed32499fa65bc39f5cb6f79a`, 1065 UTF-16 units, 23 lines, no mentions.
  Older pending rows drained; a subsequent wild result starts the next queue.
- Read-only preflush snapshot contained 61 rows, 49 wild outcomes. The existing
  renderer grouped these into 16 identities, retained separate positive and
  negative gains, and used expandable details. A later event arrived before
  flush, so this snapshot is not asserted to be the exact final sent payload.
- Two historical held batches remain unknown and are not replayed or erased.
  No notification acceptance here proves daily complete reward accounting.

## Open Entry/Readiness Observation

External fate-entry 429s are not being retried ahead of server advice:

| Identity | Failure | Retry-After | Next Actual Attempt | Outcome |
| --- | --- | --- | --- | --- |
| lalasin1 | 01:17:56 | 469s | 01:25:53 | 429, another 255s |
| lalasin1 | 01:25:53 | 255s | 01:30:59 | 200; reward settled 01:31:02 |
| jihejish | 01:31:27 | 34s | 01:36:31 | 200; reward settled 01:36:35 |
| gyurihero | 01:40:40 | 256s | 01:45:51 | 429, another 1s |
| gyurihero | 01:45:51 | 1s | 01:50:59 | 200; reward settled 01:51:02 |

The existing shared floor is 300s; it does not shorten the server deadline.
The reason for repeated external-entry throttling across roles is still open;
a low aggregate per-minute request count does not rule out endpoint/account
limits. Do not infer a specific quota or clear shared backoff to accelerate it.

Read-only rolling-window comparison through 01:45 adds a stronger clue: the
five failures from 01:17:56 onward each had exactly 46 recorded external
requests in the preceding hour. For the first four, the advised retry instant
matches the oldest observed request plus one hour to rounding precision. This
supports investigating a separate hourly entry quota, but does not establish
its official threshold, account/IP scope or coverage of other clients. The
earlier 00:38:56 failure does not fit the same count. No new throttle constant,
token reuse or retry behavior has been introduced from this hypothesis.

Gyurihero's 00:52:10 meditation rejection remains unexplained. The expected
01:22 fate recheck was delayed by shared entry backoff and later failed at
entry before reaching fate/meditation state. Until 01:51, the stored quest stayed
active 0/30, meditation_rejected, without a pending unknown action. At 01:51:00
the natural fate read confirmed it could settle; one settle and one readback
completed 30/30 with traces +3. No new meditation POST or local progress edit
was used. Original readiness values are absent from shape-only captures;
no cache/manual-operation attribution is proven. Today's quest is complete,
but the original readiness discrepancy remains open.

At 01:55, 23/24 fate records are settled. Lpprceqei alone still waits: at
01:52:10 its same-quest progress read fell from 1 to 0. The existing guard
stopped the chain without draw/choose/settle. Stored progress 1 remains only
the last accepted snapshot, not the latest server progress. An official duel
result `1305373 -> 1305375` at 01:39:08 confirmed cultivation -60000; four wild
gains of 12000 and two wild losses of 12619/12614 preceded the regressed read.
Those visible actions net -37233 after acceptance, consistent with (but not
proof of) a net-cultivation quest metric. Later actions are not part of that
calculation. Do not erase the old snapshot, disable duel or relax the guard
based on this association. Watch the natural deep-retreat end and next fate
read; the underlying progress semantics remain a separate debt.

## Trial Notification Debt

Only Lab `/root/xiuxian-trial-reward-handoff-20261007` was edited. Its original
base remains `cbe1dff8`; the optional mirror prototype and tests are uncommitted,
unmerged, unimported by production and not a release candidate.

The new experiment reserves bounded degradation metadata before a batch,
isolates optional report writes with a savepoint, preserves prior receipts and
keeps child notices on report failure. Missing durable degradation or a lost
primary transaction still blocks sending. Eleven added tests passed; related
regression: 666 passed / 3 strict expected failures / 5 subtests. A second
targeted regression: 392 passed / 3 expected failures. Full isolated Lab suite:
16914 passed / 3 strict expected failures / 1486 subtests in 457.66s, with JUnit
at `/tmp/xiuxian-trial-optional-handoff-full-20261009.xml`. Ruff/compile/diff pass.

Original end-to-end gaps remain: stable parent ownership/step keys, complete
reward coverage across retries, parent projection, delivery acknowledgement,
retention and runtime admission. This old-base Lab run is not current-production
integration acceptance; no independent review was performed. Do not remove
child notices or claim this debt resolved.
