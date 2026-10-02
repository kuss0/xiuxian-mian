# October 2 Native Fishing Checkpoint Canary

## Latest Status (07:45 UTC+8)

- Production is `8eecce9f`. Pacing and recovery-only were deployed at 03:14.
- Recovery at 03:17 accounted the first missed rod using state only.
- A second canary cast at 03:19:47, hooked at 03:20:13, and confirmed its
  2500 ms checkpoint at 03:20:15.744. Its 5000 ms checkpoint failed at
  03:20:18.392 with the same `fishing_checkpoint_stale`. The 2.648 second gap
  exceeds the declared interval: short spacing alone is NOT the root cause.
- Exact retained proof replay through the independent JavaScript fixture,
  both from zero and from the accepted checkpoint, matched all seven state
  fields including tension 57.8633009961673 and progress 53.84599999999985.
  Current official controller physics and request fields remain equivalent.
- At 07:30, scoped recovery confirmed session 48666 settled empty. Server
  quota is 2/5 used, three remaining; no additional cast was made. Its receipt
  is settled but projection is held by `native_projection_basis_changed`.
- Facts digest still matches. Inventory digest differs because ordinary loot
  changed unrelated stones and cores after the cast. Full-bag equality makes
  even a confirmed empty rod unrecoverable despite a fresh absolute bait read.
- The Lab fix rebases only a zero-catch, zero-reward settlement from a strictly
  scoped state response, with unchanged facts and before/after read basis.
  It updates only fishing balances through the existing atomic projection.
  Concurrent changes, foreign sessions, missing context and nonempty gains
  remain held. Recovery-only still cannot cast or reschedule.
- Core regression: 281 passed. Expanded isolated regression: 1378 passed,
  46 subtests passed (35.62 seconds), compile and diff checks passed.
  Deployment pending.
- SSH fetch of wxjerry `origin/main` still reports `aa9dba2`; no newer fix.
- Main/observer/watchdog active at 07:45. The foreground observer was resumed
  after the earlier session disappeared. WA's 05:27 real rift replies show
  prediction hit and retained change-fate protection; Baji's 07:32 MiniApp
  voyage return and next moon voyage both have normal confirmations.

Private additional evidence:
- `/root/xiuxian-native-fishing-recovery-20261002-0316.json`
- `/root/xiuxian-native-fishing-paced-rod-20261002-0319.json`
- `/root/xiuxian-native-fishing-recovery-20261002-0732.json`

The entries below are historical checkpoints, not the current production
version or claims of a successful catch. Native fishing remains canary-only.

## Recovery Acceptance And Readback Work (08:02 UTC+8)

- `e47e89f3` was deployed at 07:48:28 and pushed. Recovery at 07:49:54
  accounted session 48666, quota 2/5, with only rice bait changing 15 -> 14.
  Unrelated loot was preserved; no cast or timer reset accompanied recovery.
- A single diagnostic canary (session 48719) cast at 07:54:13, hooked at
  07:54:40, and again failed the second checkpoint at 07:54:46. The external
  probe did not reach state until 07:55:52, after expiry at 07:55:03. It is
  NOT evidence about the live checkpoint. Server quota is now 3/5, remaining
  two; settled missed result includes one waterweed reward.
- Recovery at 07:57:52 accounted that reward and the bait balance, but the UI
  still reported outcome_unknown and omitted the reward. Cause: the new
  store callback created a second journal, leaving the worker's journal stale.
  The candidate uses the same journal for acceptance and rebase. A real SQLite
  regression now checks returned rewards/status/unknown as well as DB commit.
- On exact checkpoint HTTP 409 `fishing_checkpoint_stale`, the candidate
  performs one immediate scoped state read while the lease is alive. It still
  does not resend checkpoint, fight, or cast. Readback failure keeps the original
  pending receipt. Allowlisted numeric observations expose reused, same-session,
  checkpoint presence/duration and server timestamps without auth fields.
- Read-only probe gained bounded checkpoint inspection. No write endpoints.
- Expanded isolated regression: 1385 passed, 46 subtests passed (35.92 seconds).
  Compile/diff checks passed; candidate deployment pending. Checkpoint root
  cause remains unproven. No broad native rollout or additional quota loop.

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
