# Rift Preparation Chain Check

Base: `9d268b72`. Only tests and documentation change. There is no runtime
patch, configuration change, game probe, live business-state write or restart.

## Investigation

At 06:19-06:38 on October 9, WA's rift deadline is 07:19:55 and its ten-minute
preparation window starts at 07:09:55. The preceding wild result consumed the
exploration prediction. The stored timeline is `blocked_replan`, without an
active step or released route. Exploration change-fate remains recorded with
complete effect evidence, Tianji is 168 and no command/reply is pending.

The generic Tianxing scheduler's next time, 07:38:57, is later than the rift.
This alone is not a defect: the completed craft target deliberately avoids
churning an old timeline. `app._run_due_explore_rift_schedulers` independently
admits a preparation candidate from `build_tianxing_consume_window`.
`explore_rift._prepare_explore_rift_tianxing_route` invokes the real timeline
scheduler without applying the generic auto timer. Deep retreat is explicitly
excluded by `app._has_tianxing_phaseful_summary_block` for Tianxing.

The business deadline is retained independently of preparation retry time.
A released route does not move that deadline earlier. Actual production
preparation and release are still pending at this checkpoint; neither code
inspection nor the tests below certify a live future action.

## Regression

`test_consumed_route_rebuilds_at_rift_lead_despite_future_auto_time` in
`tests/test_explore_rift.py` combines the previously separate regressions:

- Consumed prediction, complete remaining change evidence, stale consumed
  timeline, a reached craft target and a future generic auto timer.
- A running deep retreat whose own deadline is much later.
- Neither scheduler sends before the ten-minute lead boundary.
- At the boundary, the real planner creates exactly one exploration prediction;
  it does not send a rift or alter its cooldown.
- A repeated tick does not duplicate the pending prediction. A mismatched root
  cannot confirm it, while the anchored parsed reply can.
- Generic follow-up advances the confirmed timeline before its generic auto
  timer, but the rift still waits until the real business deadline.
- At that deadline the mocked rift transport executes the real final
  `operation_check`, records one sent receipt and does not send again next tick.

Only transport/persistence and isolated environmental lookups are mocked;
the route planner, both feature schedulers, preflight and reply reducer run.
Outer scheduler admission is also traced in source and has existing separate
tests. This new case is not a full Telegram/router end-to-end test.

Initial focused case passed. Related exploration/Tianxing lifecycle and
evidence regression passed 747 tests / 14 subtests before the final fixture
added explicit effect evidence and the final transport guard. That final
case also passed. The final full isolated suite passed 17005 tests / 1511
subtests in 445.55 seconds; JUnit is
`/tmp/xiuxian-rift-preparation-chain-full-20261009.xml`. A second maintainer
pass over outer scheduling, effect validity/consumption, read-only preflight
and rift behavior passed 345 tests / 35 subtests. This is not an independent
external review. Ruff, compilation and whitespace checks pass. Every pytest
uses `XIUXIAN_ALLOW_LIVE_TEST_DB=0`.

## Natural Observations Through 06:36

- Worker 3981692, supervisor 3981690, watchdog 3981709 and observer 3981735
  remain active from the scheduled 04:46 maintenance. No manual restart.
  Production HEAD is `9d268b72`, but worker loading still stops at `b686012b`;
  the summary repeat marker `52d8fcc2` remains pending an existing maintenance
  window and a natural repeated-row delivery.
- Lpprceqei's 06:20:50 ordinary fate read still reports progress 0 versus the
  earlier accepted 1. The guard stops the chain; no repeated reward/retreat
  action or live progress edit. Fate remains 23/24. Its next ordinary recheck
  is no earlier than 06:50:50; the running retreat is due at 10:01:57.
- At 06:28:31 native fishing completed 20/20 rods across four eligible
  identities. The daily notice receipt `248f3a4aedef40e5adb63d493e672f6d` is
  confirmed, 261 visible UTF-16 units, six lines, no mentions. The read-only
  report shows fish counts 4/2/13 for red-tail/silver-whisker/green-scale;
  the final empty rod is not represented as an extra fish.
- The separate routine receipt `8378b5e783004538b0eba2bd706ed19f` at 06:29:30
  is confirmed, 252 units, six lines, no mentions. It is not a duplicate of
  the fishing daily notice. Both historical unknown batches remain held.
- WA's 06:32:32 native return confirms cultivation +325, stones +84, soul
  wood x1 and affinity +7; this row is present in the ordinary summary queue.
  After status/fortune/dream commands, three heart choices settle once on
  official edited reply 1306736 at 06:34:07: cultivation +781, affinity +7,
  heart demon -5/current 0. Heart session is complete, voyage is sailing again,
  return 12:34:18, maintenance 12:36:13. No manual game action was issued.
- WA/Baji duel and incense refinement remain off; harvest and voyage remain on.
  Read-only preflight still reports the known xuruode1 frozen-voyage coverage
  gap and intentionally inactive sidecar. Neither is claimed fixed here.

Foreground monitor 18303 remains active. Continue through the next ordinary
summary and the natural 07:09:55/07:19:55 preparation/release windows.

## Natural Acceptance At 07:24

Test/documentation commit `9a1a4052` was merged and pushed without restarting.
Production-directory isolated regression passed 72 tests / 5 subtests. The
runtime remains loaded through `b686012b`; this is acceptance of that existing
runtime path, not a newly deployed behavior fix.

The outer scan admitted WA at 07:10:10. Exactly one `.推命 探索` was sent at
07:10:11 as 1306921 in group -1002083016447. Official bot 8980154525
(`hantianzun23_bot`) replied with 1306923, rooted at 1306921, server time
07:10:14, received 07:10:16. The effect record and active step are confirmed;
the route released at 07:10:17.865. At 07:17:13 the read-only preflight still
confirmed both protections, and no additional WA command had been sent since
the prediction. The original rift deadline stayed 07:19:55 throughout.

One `.探寻裂缝` was sent at 07:19:57 as 1306958. Official bot 8917921351
(`hantianzun31_bot`) acknowledged on 1306959 at server time 07:19:59 and edited
that same anchored message to final success at 07:20:07. The receipt records
gold/wind law fragments x1 each. Prediction hit gives Tianji +1 / contribution
+30; current Tianji is 169, prediction consumed, remaining exploration
change-fate confirmed for 17h58m and calamity count 0.

All rift pending/reply/manual-required fields are clear, error empty, next
business time 19:19:59. The consumed timeline returns to `blocked_replan`,
which is expected after downstream consumption, not a new stuck action.
The cooldown-preservation/independent-preparation success branch is naturally
accepted. Unknown transport, missing replies and future cycles retain their
guards and are not certified by this one successful event.

The rare-result notice `a7b1ae5677ba4ae6906394278a872a9b` is confirmed at
07:20:08: 89 visible UTF-16 units, three lines, one mention. This is an
important-result path, not an ordinary wild-summary regression. Earlier,
the queued return/start/tower summary was confirmed at 07:02:34 as
`8a72ab018a6941d4a64d186f3fae46b7`, 239 units/five lines/no mentions; rows
drained. An unrelated player's 07:00:46 quiz timeout was console-only through
the external-observation branch in `quiz.py`, without a separate TG delivery.
Two historical unknown batches remain held, never replayed or removed.

At 07:21:51 Baji queried its small world once (1306971 -> 1306972), then
manifested once (1306973 -> 1306974). Official success at 07:22:07 reports
faith +5, stability +4 and a 360-minute prayer cooldown; stored faith is 100
and error empty. Incense refinement remains off for both Baji and WA.
Lpprceqei's separate 07:22:02 fate read still reports 0 versus accepted 1;
that pre-existing progress issue remains open, next ordinary read not before
07:52:02. No game state, recovery timer or module switch was manually changed.

Supervisor/worker/observer/watchdog PIDs remain unchanged. Monitor 18303 is
still active; continue normal monitoring, including the upcoming harvest and
nascent-soul windows. This document is a checkpoint, not a monitoring handoff.

## Read-Only Debt Check Through 07:52

Log-bot callback polling had one connection reset at 07:35:39 and recovered
at 07:35:57, without intervention. The 8-hour direct-reply report has 6/6
duel commands, 13/13 Tianxing commands and 1/1 rift with replies. The six duel
commands include the target-cap rejection, not six completed battles. Local
first/final latency P95: duel 6s/23s, Tianxing 5s/5s; the single rift is 3s/10s.
Small samples and direct-root coverage are not universal timeout defaults.

The shadow-only checkpoint at 07:38:14 reports 28639 attempts, 4289 blocked,
32 historical send_unknown and two queued records from July 14; no last-day
stale transport/error and no resend_count increment. The primary database is
69136384 bytes; filesystem availability is 38G. Of retained-log attempts,
3949/3949 root IDs appear in sent logs, but all 3949 lack ledger chat scope.
Therefore parity is only `partial_id_only`; stored exact-bind labels are not
an independent precision audit. Gate 4 stays closed and nothing was archived,
deleted, recovered or retried from this report.

Today's semantic report counts 1911 captured requests, maximum 46 per minute
against the configured 90 threshold. This excludes unrecorded clients and
does not disprove a separate endpoint/hourly limit. Its six transient and one
application error are the earlier entry429/meditation409 observations, not a
new active incident. Two faith intervals remain unexplained by logged events:
Baji 97 to 95 from 01:08:02 to 07:21:53; WA 99 to 98 from 01:09:05 to 03:41:24.
Do not infer theft, decay or a strategy fault without evidence. The later
confirmed Baji manifestation restores faith to 100. No added preaching,
disaster relief or incense refinement was performed.
