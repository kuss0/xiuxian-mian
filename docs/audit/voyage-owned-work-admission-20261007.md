# Voyage Owned-Work Admission

## Scope

This six-line runtime fix only makes public MiniApp voyage admission respect
the existing concubine owned-work gate. It rejects pending or malformed
heart/query/gift/fragment/voyage work before login, and returns a terminal
blocked decision to the caller instead of trying the Telegram mutation path.
No timers, business projections, schema, HTTP retries or switches are changed.

Normal confirmed returns, including when the next-launch switch was disabled,
remain available. Manual identity disable still blocks HTTP; channel-health
freeze retains the existing public-entry exception. Unrelated retreat and
status work do not block this route. Other transport outcomes retain their
existing policy, not a newly certified recovery guarantee.

## Evidence And Tests

Baseline: 18 failing regression cases and six passing controls. Fixed focused
regression: 560 passed across the new admission tests, public Tianjige,
owned voyage actions and queries. Second review found and corrected an
overbroad block of read-only status fallback: only mutations now stop on
`blocked`. Post-correction cross-module regression: 453 passed. The initial
full suite passed 16875 / 1486 subtests; final frozen-code suite passed
16876 / 1486 subtests. Ruff/compileall/diff checks passed. This is code
acceptance only; loading and natural runtime acceptance are separate.
Deployment evidence will be appended after completion.

All pytest commands set `XIUXIAN_ALLOW_LIVE_TEST_DB=0`; all transports are
fake. No live requests, state corrections or switch changes were used.
An isolated in-memory replay of current WA/Baji business rows using the
same admission helper returned clear; neither currently has owned pending
concubine work. Both remain sailing with future return deadlines.

## Explicitly Not Resolved

The broader asynchronous snapshot/cancellation proposal is not included.
Its Lab at `/root/xiuxian-voyage-public-guard-20261007` passed 16917 tests /
1486 subtests, but second review then found two release-blocking gaps:

- An unknown launch merely sets retry_count=2 and delays an hour; it can
  subsequently become eligible without authoritative reconciliation.
- A cancelled dispatched result can be submitted again on the next tick.

Two strict expected failures in that Lab preserve these findings. Do not
merge the broader worktree or call its unknown recovery complete. Durable
binding, pre-dispatch persistence, definite-unsent handling, local result
replay and guarded read-only reconciliation must cover both MiniApp and
command paths before automatic admission is expanded.

The frozen-channel xuruode1 voyage is also unresolved: enabled voyage,
disabled channel profile, July 25 return time, July 19 snapshot and August
14 schedule. App due scans exclude the identity. Its HTTP-only scheduling
needs fresh game evidence and must not run the ordinary command-capable
concubine recovery chain. No stale state was used to trigger it.

## Live Checkpoint

At 13:24 CST October 7 the production worker still loads `51c2e348`,
supervisor 2901469 / worker 2901475; watchdog and observer are active.
No restart occurred. New foreground follower 76892 runs until about 16:25;
old follower 33330 may expire normally at 13:35.

At 13:35 the old follower expired normally and 76892 remains active. Worker
and monitor PIDs are unchanged. Current MiniApp capture evidence is 2390
requests, peak 44/90 and one earlier transient. Two small-world faith deltas
are still unexplained. Refinement and duel remain off for WA/Baji; balances
are 205913 / 248269. No empty notification was sent at the 13:33 window.

13:03:23 ordinary summary `b901bec08a684126b3add65e4372ae6a` was confirmed,
105 UTF-16 units / three lines / no mention; queue empty, historical held=2.
13:21:20 WA's one pet command 1292055 received official reply 1292056 two
seconds later. No extra command was sent for testing. Existing notification
changes remain pending runtime loading; this observation does not certify
unmanaged-quiz suppression.
