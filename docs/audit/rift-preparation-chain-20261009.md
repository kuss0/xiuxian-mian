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
