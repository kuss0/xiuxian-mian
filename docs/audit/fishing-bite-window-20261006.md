# Fishing Bite Window

Base: `84fbcffc` (summary deadline fix included). Status: `99a43a45` deployed
and pushed. Separate maintainer review passes, not external reviewers.

Live evidence: xueuode5 2026-10-06 00:13 CST, cast RTT 388ms followed by
hook RTT 107ms and HTTP 409 `fishing_too_early`. The original rod was later
read back, settled empty and accounted; it was not blindly recast. Other
rods completed normally. Midpoint server-time estimation can be ahead of
the real clock when a cast is slow upstream and its hook is faster.

Two independent-server-clock replay cases failed before the patch; normal
symmetric and downstream-heavy cases passed. The bounded opening cushion is
min(RTT/2, (window-RTT)/2). It never spends the full-RTT expiry reserve. When
the window cannot accommodate the observed RTT, only a state read is allowed.
The existing 3262ms asymmetric four-second-window replay still succeeds.
No quota, retry, ownership, checkpoint/fight physics or scheduling changes.
Cast observations now retain allowlisted numeric bite/expiry/server timestamps;
no token, player payload or session identifier is added to diagnostics.
Expected sailing waits remain a separate notification classification debt.

## Acceptance

- Focused protocol, flow and durable journal: 210 passed.
- Frozen combined full suite: **15909 passed, 1436 subtests**, 463.14s;
  `/tmp/xiuxian-fishing-bite-window-20261006.xml`.
- Second review: **589 passed**, including SQLite settlement, caller/worker
  cancellation, unchanged voyage waits, result authority and the summary store.
  Ruff, compileall and diff checks pass.
- Re-read unknown hook/fight recovery, actual-dispatch window checks, rate
  budget, no-RTT fixtures, slow asymmetric latency and capture redaction.
  No remaining blocker found for this scope. Severe unpredictable latency can
  still miss a bite: no claim of guaranteed catches or post-patch live quality.

Frozen runtime hashes:

```text
4c7e05aa30ddfe47e469641783f25faece10160fcc8406185281f5b92ba0a1ac  model/features/fishing_dwelling_protocol.py
8bf08d5212bda597bf5f77b67b0990ebff879dc5a76bba82084a72f1e13da5a2  model/features/fishing_dwelling_miniapp.py
```

## Release Boundary

Promote with the independently accepted summary deadline commit in one
controlled restart. Preserve all identity settings, fishing quotas, held
notifications and Tianxing scheduling. No manual cast or target activation
for acceptance. Back up both SQLite databases before changing the service;
rollback code only, never restore old game state.

At 00:39, xueuode5 and Lpprceqei both had 5/5 accounted rods under the old
code. WA/jfdffdddd still wait for their known voyage returns. Native timing
quality after this patch therefore remains a natural-sampling gate.

Other live observations are not silently declared fixed: xuruode6 received
`meditation_not_ready` (409), saved `meditation_rejected` without pending;
xuruode1 received `external_action_rate_limited` (429) and the shared entry
deadline was persisted at 00:40:41. Observed global peak 32/min was below
90/min, which does not rule out a separate endpoint restriction. WA Yuanying
completed after the shared wait. Retain these as follow-up observations.

## Production Checkpoint

Both commits were fast-forwarded into production after the old worker exited.
00:44:52 restart, 00:45:14 bootstrap, 192 post-merge isolated tests passed;
push to `xiuxian-mian/main` completed. All 24 identities, 59 enable columns and
full MiniApp configuration match the pre-stop snapshot. No fishing toggles,
gifts or Boss participants changed. Same backups and held comparison as the
[summary rollout](summary-idle-window-20261006.md).

At 00:46, no active fishing journal or game pending; watchdog ok. Ordinary wild
results are naturally enqueued for summary. WA preparation/execution stays at
01:00:58 / 01:10:58. Hook timing after deployment is not yet naturally sampled
because the two early fishing identities already exhausted today's quota.

The old worker logged incomplete cleanup during stop, associated with the
15.622-second fate interpret request for mudamuda0. Its successful result is
saved as `interpret`, with no pending action; do not force replay. This remains
an explicit shutdown-drain/continuation follow-up, not silently cleared by
the unrelated timing patch.
