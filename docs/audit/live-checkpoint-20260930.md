# Live Checkpoint: 2026-09-30

Scope: passive verification at 03:12-03:19 Asia/Shanghai on `b111a274`.
No runtime patch, live-state mutation, manual game request or restart.

## Verified

- R123 cross-day trial acceptance is complete. See
  [the scoped acceptance](R123-trial-cross-day-unknown-archive-20260929.md).
- All 19 channel identities have a current-day MiniApp deep-retreat start
  record with `ok=true`, verified identity and `outcome_unknown=false`.
  Their Telegram send-as freeze does not block these HTTP actions.
- Eighteen wild-training identities have current-day completed snapshots,
  daily count/limit 8/8 and remaining 0. Four Tianxing channel identities remain
  blocked by unavailable Telegram preparation, not counted as completed:
  `xueuode5`, `growrdick`, `xuruode6`, `tutuerduoxiao`. Wild training remains
  disabled for `jfdffdddd` and `wisemole`.
- WA's latest raw wild result at 00:55:19 explicitly contains prediction hit,
  Tianji +1, contribution +30 and change-fate still pending. The reducer cleared
  the consumed prediction, retained the confirmed change-fate effect, and left
  no pending auto command. Today's eight prediction sends correspond to eight
  wild results; the final snapshot confirms 8/8, not a guessed local quota.
- `jfdffdddd` voyage automation is enabled, route is Moon Palace, and current
  state is sailing. Return is due 06:26:10; the next module check is 06:35:59.
  WA is also sailing, return due 07:05:52. These are not return acceptances.

## Reconciled Incidents

- September 27 WA prediction `1213339` timed out without a matching reply in
  the retained message log. The later panel was positively owned by WA:
  command `1213381` at 04:10:17, reply `1213382` at 04:10:19 in
  `-1002083016447`, showing no active prediction/change. Prediction `1213383`
  followed at 04:10:43. This retry was preceded by authoritative calibration,
  not an unconditional resend after a timeout.
- September 29 predictions `1228690`, `1228698`, `1228704`, `1228717`,
  `1228766`, `1228774`, `1228786`, `1228795` align with eight distinct wild
  completions between 00:24:54 and 00:52:52. Prediction `1229981` belongs to
  the later rift, whose final reply `1230033` explicitly confirms prediction
  hit at 04:37:42. Old local counters alone are not used as consumption proof.
- Lsfnqy fate-cards hit `meditation_not_ready` at 01:14:00. At 01:44:26 the
  existing scheduler received a successful fate settlement, followed by a
  state read. Persisted quest is verified, settled, progress 30/30 and gains
  trace +3. Captures show only one draw, interpretation and choice in this
  chain; the later run did not repeat them. The observer still reports the
  original failure in its three-hour historical window. No success of the
  rejected meditation itself is claimed.
- `myios17` dynamic fate entrance failed at 00:53:02 and recovered at 00:58:23.
  It is now waiting for deep-retreat progress, not falsely marked settled.

## Remaining Watch

1. Fate-cards: 8 settled, 16 waiting for deep retreat. Verify later settlement
   and one combined daily reward report; do not mark the whole day complete.
2. WA rift due 05:03:03: recheck preparation near 04:53, requiring fresh
   prediction and valid change-fate evidence before release.
3. Nineteen send-as identities remain frozen with `SendAsPeerInvalidError`.
   Do not remove the freeze or bypass Tianxing protection for coverage numbers.
4. Small-world stale prayer deadline repair: offline reproduction and regression
   validation completed below; post-deploy observation remains required.
5. Listener sidecar remains inactive; runtime listener is the only receiver.
   World Boss stays disabled, inventory reads manual-only, Attempt shadow-only.

## Validation

- Tianxing/wild/rift lifecycle and defensive preflight: 383 passed.
- Trial operation/runtime selection: 115 passed, 115 deselected.
- Pytest isolation confirmed in `tests/conftest.py`; no live DB testing.
- Watchdog dry run: OK. Defensive preflight: watch, pending queue empty.
- Main service, health observer and watchdog are active, all with
  `NRestarts=0`. A separate foreground observer consumes 30-second checks.

This is a dated checkpoint, not a claim that all project debt is closed or
that later scheduled actions have already succeeded.

## Small-World Retry Repair: 03:33 Follow-Up

The September 28 timing discrepancy is reproducible in the module, without
changing shared transport or recovery:

- Panel `1220767` at 03:53:57 reported a 21,599-second prayer wait.
- Query `1222496` was sent at 09:58:54 without a retained matching reply.
- At 10:19:02, module timeout scheduled retry for 10:32:39. However, the next
  scheduler tick reconciled the old panel's already-elapsed prayer deadline
  over that retry timer. Query `1222784` was sent at 10:19:13.
- `_reconcile_cached_prayer_deadline` now rejects an elapsed cached deadline
  as scheduling authority. Future valid prayer deadlines still shorten an
  overlong timer. No new state field, alternate sender, resource-policy change
  or blanket pause was added.
- The regression first failed against the old code: after timeout, the next
  ticks queried before the scheduled retry. It now verifies recovery is tried
  first, the retry remains intact through its last second, and a query is
  allowed at the actual retry deadline. Additional boundary cases cover a
  countdown expiring exactly now and one expired 25 minutes ago.
- Small-world suite: 106 passed, 2 subtests. Expanded small-world, native
  lifecycle, startup, scheduler, phaseful, watchdog and observer suite:
  698 passed, 36 subtests. Python compilation and diff checks passed.
- Pre-deploy state: pending queue empty; both small worlds idle and their
  refinement switches remain off. WA's next small-world action is 04:05:35;
  Baji's is 07:16:49. No old timer was manually rewritten.

This repairs a scheduling defect; it does not assert that the missing original
reply was ever received, or that query retry is free of in-game cost.
