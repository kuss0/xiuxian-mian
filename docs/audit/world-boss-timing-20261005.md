# World Boss Timing Review

Base: `1d5eb773`. Lab: `/root/xiuxian-boss-timing-20261005`.
Status: candidate accepted; deployment checkpoint will be recorded below.
Reviews below are separate maintainer passes, not independent external reviews.

## Natural Evidence

Source: `data/messages/miniapp-captures/world_boss-2026-10-05.jsonl`.
The October 5 13:40 event had one authorized participant, `301299112`.
Initial begin returned an explicit verification rejection; `begin_verified`
and finish subsequently returned HTTP 200. No change to that flow is needed
to explain this event's low perfect rate.

- Sixteen distinct windows were disclosed, fifteen were charged and hit,
  eight hits were authoritative perfects. Finish reported positive damage,
  player HP 84, not dead, and `full_window_run=false`.
- The missing window was number 14 (`w_e9vgKmNe7qur`), between the thirteenth
  accepted hit and the final two hits. Its ID appears as the next reveal
  cursor but never in a charge/hit request. Proof bestCombo=13 and final
  combo=2 agree. Earlier backlog wording "missing last hit" was incorrect.
- Window 6: local requested hold 1076ms, server hold 1610.7ms, hit RTT 683ms.
  Window 11: requested hold 1212ms, server hold 1283.9ms, hit RTT 190ms.
  The near-ceiling hold leaves insufficient headroom even for modest jitter.
- Windows 8/12: charge RTT 810/811ms, followed almost immediately by hit;
  server holds were only 158.6/172.4ms. Treating the entire charge request
  duration as confirmed server charging is not valid with asymmetric latency.
- There were 98 captured HTTP calls from verified begin through finish;
  the Boss-only rolling 60-second peak was 69. This is not evidence of a
  global rate-limit breach. It also does not prove where each delay occurred:
  captures aggregate local queuing, transport and server response time.
- Old captures contain response shapes, not dynamic window center values;
  they cannot establish the exact remaining milliseconds when window 14
  was skipped. The old admission guard requires 520ms before the early
  release target. Its numeric context was not persisted by the runtime.

## Narrow Changes

1. Default planned hold is 1100-1150ms, leaving at least 100ms rather than
   15ms below the 1250ms ceiling. This sacrifices some charge multiplier
   for moderate-jitter tolerance, not a promise of higher leaderboard rank.
2. After a slow ticket response, extend the remaining hold only within the
   server's disclosed perfect/hit bounds and the local 1250ms cap. Keep a
   conservative arrival margin. If the response is already late, do not add
   another full hold. There is no inferred exact server issuance timestamp.
3. Report actual elapsed local press duration in the hit and proof, not the
   original planned duration when they differ. Server perfect/damage remains
   authoritative for the accepted summary; local proof statistics are distinct.
4. Recheck eligibility after sleeping to the charge start. A late thread wake
   cannot submit a charge against a now-expired window and abort the rest of
   the flow. Expired ticket responses similarly do not produce late hits.
5. Persist bounded, secret-free window timing, skip reason/index, local hold
   and charge/hit RTT alongside existing authoritative business captures.
   `skipped_window_count` counts actual loop skips, not intentional configured
   tail skips or undisclosed windows. Existing full-run calculation is unchanged.

No change to Turnstile, entry selection, account switches, global 90/min limit,
poll cadence, transport retries, unknown-send policy, or notification routing.
No new HTTP calls, forced games, extra participants or live state edits.

## Acceptance

The independent fake server records actual arrival/ticket times and computes
perfect from those times. Tests do not return unconditional perfect results.
Counterfactual tests reinstate the former hold constants and RTT subtraction;
they reproduce excessive and insufficient server hold with the same clock.
Other cases cover asymmetric delay in either direction, irrecoverable latency,
late scheduler wake, expired charge response, truthful local duration, bounded
extension, skip capture and exactly one finish with no hit replay.

Focused regression: **293 passed, 43 subtests**. Ruff and diff checks passed.
Two earlier whole-suite runs were intentionally interrupted while narrowing
the margin and adding the post-sleep guard; neither is acceptance evidence.
Final frozen whole-suite acceptance: **15876 passed, 1436 subtests**, 447.18s.
JUnit: `/tmp/xiuxian-boss-timing-accepted-20261005.xml`.
Second review re-read the helper's late-response bounds, post-sleep admission,
single-use ticket dispatch, static/dynamic protocol compatibility, authoritative
summary/proof separation, and diagnostic failures. No remaining release blocker
was found for this narrow change. Second-pass regression: **420 passed,
47 subtests**, including observer and semantic report compatibility. Compilation,
Ruff and diff checks pass. All tests used `XIUXIAN_ALLOW_LIVE_TEST_DB=0`;
the frozen hashes below match after both passes.

Frozen source/test SHA-256:

```text
c9df304b5152e8839531b3e0599906044a886e2d12a4b6fb58fcfdb51d6ad501  model/features/world_boss_miniapp.py
f2cc6bd6ced3fcf6f71d0f708ba08511c06b0f746ab9e5783482a1df41466434  tests/test_world_boss_timing.py
e33ab59f0b1bc19bb8f2629d3932a90b49a3964eeca7a8f391109e125af01107  tests/test_world_boss_miniapp.py
```

## Deployment Boundary

After full acceptance and second review, deploy once outside an active Boss
or gameplay pending window. Snapshot the game and notification databases;
preserve all 24 identities, module/config switches and historical held batches.
Restart only the main service, not observer/watchdog. No schema migration is
required. Rollback is code-only after the battle completes, never restoring an
old gameplay database. The runtime quiz-bank changes must not be committed.

## Still Open

- Natural post-deployment perfect rate, charge distribution and net damage.
  Unpredictable 600ms+ delays cannot be repaired by a fixed local timing margin.
- Window 14's exact numeric admission failure; new capture supplies the
  previously missing evidence. No broader polling/admission change is approved
  by this patch, and 16/16 has not been demonstrated on production.
- Boss no-new-fact notification dedup needs its own natural event.
- The old October 5 09:11 summary delivery remains unknown, not replayed or
  cleared. At 21:43 the notification queue has zero normal rows and one held
  batch. This is unrelated to Boss timing.
