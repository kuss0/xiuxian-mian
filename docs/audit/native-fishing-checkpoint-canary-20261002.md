# October 2 Native Fishing Checkpoint Canary

## Live Evidence

Production remains 511b161e. Yesterday's missed rod was accounted by the normal
scheduler at 00:03:14. The server context then confirmed today's quota 0/5,
not yesterday's remaining three. Public ordinary scheduling still looks for
the external entry and reports integrated fishing as unavailable; this is not
evidence that the selected identities actually fished today.

For identity 7538826434, one configured chum was confirmed at 00:58:37. The
first subsequent UI request returned the exact local public-lock busy guard;
it made no game requests. A separate bounded invocation allowed waits only for
that verified pre-run guard. No HTTP/game error or timeout was retried.

The admitted rod produced:

| Time (UTC+8) | Action | Result |
| --- | --- | --- |
| 01:07:34 | cast | HTTP success, 907 ms |
| 01:07:55 | hook | HTTP success, 120 ms |
| 01:07:57 | checkpoint 2500 ms | HTTP success, 110 ms |
| 01:08:00 | checkpoint 5000 ms | fishing_checkpoint_stale, 100 ms |

The second checkpoint intent remains retained. No fight/final submission or
second cast was sent. A scoped state read at 01:15:55 returned the same session
missed by timeout, caught=false, no bonus loot, quota 1/5 used and four remaining.
This read did not write production state or clear its durable pending intent.

Private evidence:
- /root/xiuxian-native-fishing-supply-20261002-0058.json
- /root/xiuxian-native-fishing-rod-20261002-0103.json (local busy only)
- /root/xiuxian-native-fishing-rod-20261002-0110.json (actual rod)
- /root/xiuxian-native-fishing-checkpoint-state-20261002.json
- Production fishing-2026-10-02.jsonl and retained native operation.

## Lab Pacing Candidate

The observed checkpoint response times were 2.491 seconds apart while the
challenge declares a 2500 ms checkpoint interval. The absolute fight clock
allows a delayed prior response to compress the next request's interval.
Three offline reproductions with 20/300/1000 ms reply delay demonstrate this.

The candidate also waits the declared interval after the prior confirmed
checkpoint response. It retains the original real-time proof clock, ownership
checks, save-before-send and stop-on-unknown behavior. It neither retries nor
ignores fishing_checkpoint_stale. Final settlement is not delayed by this
additional checkpoint-only rule.

Server-side stale semantics have not been established. This is a conservative
pacing candidate, not a confirmed complete explanation of the live rejection.
Expanded isolated regression: 2249 passed, 196 subtests passed, 37.42 seconds.
The candidate is Lab-only; production was not restarted for this change.

## Other Observations

- WA's actual final wild response at 00:56:58 confirms 8/8, prediction hit and
  change-fate rescue. It is not just a timer-based completion claim.
- Trial batch completed execution 12/12 with 11 successes at 01:11:57; the
  failed step is retained for isolated retry. Do not claim 12 successes.
- Health at 01:24:44 is warn, score 93: one recent MiniApp nonterminal failure
  from the fishing canary. Pending task and ordinary module queues are empty;
  native checkpoint intent remains separate and must not be ignored.
- Native daily summary has a further bookkeeping discrepancy: the recovered
  previous-day rod appears as one summary rod today although today's used
  quota is zero before the canary. Preserve this as a follow-up, not proof of
  an extra real cast. No live DB patch was made.
