# Live Checkpoint: 2026-10-01

## Revision and Control Boundary

- Production: `/opt/xiuxian-main`, `main`, `f69f694e`, clean at the evening check.
- Lab: `/root/xiuxian-native-fishing-20260930`, `lab/native-fishing-20260930`,
  `39553988`, committed and pushed. Its timing and gift-queue changes are not
  deployed. Earlier production changes remain documented in the native Lab log.
- Service restarted at 04:48:57 without a Git revision change. This agent did
  not initiate that restart. Evening PID 3804393, NRestarts 0, active.
- Foreground observer session 15289 remains active and has been polled after
  resumptions. Background observer and watchdog also run; do not equate their
  uninterrupted operation with uninterrupted foreground agent observation.
- Preserve World Boss off, incense-to-shenshi off, channel-send freeze,
  manual-only inventory queries and CommandAttempt shadow-only control.

## Receipt-Backed Business Checks

- All 24 latest deep-retreat snapshots are dated today and report running,
  without outcome_unknown. Morning journal has settlement/start pairs; the
  afternoon boxboxji initial-read timeout recovered at 17:35:54 before start.
- Today's wild-training snapshots confirm 18 identities at 8/8. Four historical
  July snapshots are excluded, not presented as current execution. WA had eight
  journey action responses and eight prediction commands in its morning batch.
- WA rift results at 05:13:36 and 17:25:26 confirm prediction consumption,
  Tianji +1 and contribution +30 each, with change-fate still available.
- Fate cards: all 24 records have today's settled quest and settlement receipt;
  the combined daily report was emitted at 05:23:20. Earlier rejected meditation
  and rate-limited external entry are not unresolved incidents at this checkpoint.
- Trial batch completed 12/12 at 05:17:35. myios7 stargazer recovered at 01:44:59
  with material collection and eight new star pulls.
- Both main companions returned and started another moon voyage repeatedly today.
  At 08:12 Baji harvested 5706 incense to stock 143503; at 08:25 WA harvested
  5689 to stock 375106. These are point-in-time stocks, not current live balances.
- Native canary identity 7538826434 remains 1/5 with an accounted empty-catch
  receipt; today's captured mutations contain one cast and one hook, no
  checkpoint or fight. No further casts were authorized by this checkpoint.

## Investigated Alerts

- 12:47 and 13:48 watchdog bursts were three-round heart trials interleaved with
  other accounts' work. Each choice has its next-round/settlement edit, and both
  session records are complete. No duplicate round or blind resend was found.
  The watchdog warning threshold was not loosened or disabled.
- External fate-card entry genuinely returned 429 at 01:51:42, 04:54:40 and
  04:59:46, with retryAfter 49, 282 and 7 seconds respectively. Later entry and
  quest settlement succeeded. The first incident's surrounding captured peak
  was 36/min; this does not prove the server has no separate endpoint quota.
- Log-bot callback connection resets at 14:17 and 19:58 recovered on the existing
  retry path. No additional retry layer was introduced.
- At 20:14 health was ok and pending queue empty. The inactive listener sidecar
  is still a known watch item; the main listener is handling replies.

## Remaining Gates and Next Check

- Native fishing still needs one complete real catch, including checkpoint and
  final fight settlement. Offline tests and timeout accounting are not that test.
- Timing fix 789073a7 is a midpoint RTT estimate, not a guarantee against
  asymmetric network delay. It remains held with the native gift-queue bridge.
- Gift-queue bridge 39553988 passed 2259 tests and 196 subtests, including atomic
  save/reload, rollback, deduplication, changed controls and existing gift handoff.
- Voyage-return handoff, native fish-open contract and public-selection/module
  switch semantics remain unresolved rollout prerequisites. Do not bulk-enable
  native fishing or delay existing voyages to make a test pass.
- Latest SSH fetch of wxjerry remains aa9dba29; no newer revision was found.
- Watch midnight daily resets and WA's next wild-training preparation window
  around 2026-10-02 00:27:35, ahead of its 00:37:35 timer. Recompute these times
  from current state at resume. No unrelated debt cleanup was deployed today.

## 20:33 Lab Cleanup Boundary Follow-Up

Inspection found `_retire_legacy_fishing_state` checked only legacy operation
and result receipts, unlike the reset, initial-check and status paths. Direct
retirement could therefore clear stale legacy waiting fields and move the
timer while a native cast or supply receipt still needed reconciliation.

Four direct-call reproductions failed on the old implementation, covering
valid pending and corrupt native cast/supply records. Four scheduler-path
cases were already protected upstream and passed before the change; this is
not evidence of a current scheduler incident or confirmed live corruption.

The one-line fix reuses `_fishing_has_unresolved_result` instead of adding
another definition of pending work. All eight reproductions now pass; the
expanded suite passes 2267 tests and 196 subtests. Compile and whitespace
checks pass. This fix also remains Lab-only with the prior native candidates.
No production state, command behavior, switch or retry policy was changed.
