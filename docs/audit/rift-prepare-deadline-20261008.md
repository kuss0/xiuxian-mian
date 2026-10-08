# Rift Preparation Deadline

## Incident

October 8, 2026, game group `-1002083016447`, WA `8659059191`:

- Prediction `1297773 -> 1297774` at 07:07:25; change-fate
  `1297775 -> 1297776` confirmed at 07:07:42.
- The scheduler pulled the future rift timer forward at 07:07:45.
- Rift `1297777 -> 1297778` at 07:07:55/57 was refused with an explicit
  3 minute 41 second cooldown. A later send `1297784` at 07:11:44
  completed normally. This was an early send, not a lost reply.

The old pull-forward helper used the `explore_rift_last_result` display
string to decide that `next_explore_rift_time` was a preparation retry.
The preparation path also writes that string before the real cooldown
expires, so it could not distinguish the two timers.

## Change

- Keep the business deadline in `next_explore_rift_time`.
- Store all Tianxing preparation waits in the existing persisted
  `explore_rift_tianxing_prepare_retry_at`, including overdue preparation,
  resource shortage and conflicting predictions.
- Remove the display-string-based pull-forward helper.
- Once the business deadline is due, a ready route may bypass only the
  preparation wait. Existing preflight and dispatch guards still run.
- Fast scanning shares the same preparation predicate. A waiting identity
  cannot occupy the only scan slot ahead of another runnable identity;
  pending-reply recovery remains ahead of that filter.

No schema migration, live timer rewrite, retry-policy relaxation, new game
request, configuration change or CommandAttempt control takeover. Legacy
future timers stay conservative until due. No deep-retreat condition was
added to Tianxing. The separate unfinished voyage Lab is not included.

## Validation

All pytest runs use `XIUXIAN_ALLOW_LIVE_TEST_DB=0` and isolated state.

- New regression baseline: 11 failed / 6 passed on the original code.
- Official cooldown replay, real preparation-to-ready transition and
  temporary SQLite save/reload cases pass after the fix.
- First cross-module pass: 2617 passed / 68 subtests.
- Second review reproduced scan starvation: 1 failed / 2 controls passed;
  fixed by sharing the preparation predicate with the fast scanner.
- Scanner/ownership regression: 111 passed / 194 subtests.
- First full pass: 16970 passed / 1486 subtests, 454.31 seconds. It predates
  the scanner correction and is not the final release validation.
- Final frozen full run: 16973 passed / 1486 subtests, 446.28 seconds;
  `/tmp/xiuxian-rift-prepare-deadline-final-20261008.xml`.
- Final cross-module review: 2598 passed / 262 subtests (rift, Tianxing,
  wild training, scanner/ownership, preflight and schema contracts).
- Ruff, compile and diff checks passed. No runtime edits after the final run.

## Live Observation

Resumed October 8 at 21:35. Before deployment, worker `3435080` had been
running since 04:49:17 after a normal service stop/start, not a crash loop.
Watchdog and health observer were active, NRestarts zero. Main owns game
listening; the independent listener remains disabled as previously recorded.

- WA's previous unobserved return settled October 7 at 23:41:54, then
  relaunched once. October 8 has three further returns for each main owner.
- Baji harvested 5818 / 5866 / 6028 incense, total 17712; WA harvested
  8038 / 7916 / 8507, total 24461. Stocks at 22:05 were 271916 / 238941.
  Both automatic refinement and duel switches remain off; voyages remain on.
- WA's eight wild actions ran 00:32-01:03. All eight preceding predictions
  have official anchored replies; two additional change-fate replies were
  confirmed. Five outcomes gained 45000 cultivation each; three escaped
  through change-fate. No deep-retreat deferral occurred.
- Fishing saved evidence: four identities settled five casts each; thirteen
  lacked a companion and six lacked a rod. No fishing evidence warnings.
- Supplied October 8 runtime notification receipts through 21:30:
  47 attempts / 47 confirmed, zero repeated confirmed payloads,
  visible UTF-16 P50/P95 153/1125. Other uninstrumented senders are excluded.
  Two historical held batches remain untouched; the ordinary queue was empty.
- Captured MiniApp request peak was 72/90 per minute. The report retains
  two application errors and five transient errors, not a zero-error claim.
- Callback polling had six failures at 09:11-09:14 and recovered 09:15:06;
  the observer warning appeared and cleared without a service restart.
- At 22:01:53 both owners received their own earthquake broadcast in the
  other monitored group. Each sent one preach command in the active group:
  WA `1303925 -> 1303926`, Baji `1303927 -> 1303928`. Both confirmed,
  consuming cultivation, not incense. No duplicate or pending reply remained.

Unresolved: frozen-channel voyage coverage for `xuruode1`, two historical
held notification batches, partial small-world faith-delta attribution and
the separate voyage durability integration. None is closed by this patch.

## Release

Code validation complete; merging and controlled loading are pending.
No natural post-fix rift event has been accepted yet. Next WA rift is
October 9 at 07:19:55; next wild run is 01:22:14, with preparation ten
minutes earlier. Do not reset either timer to force acceptance.
