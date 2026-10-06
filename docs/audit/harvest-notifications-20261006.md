# Routine Harvest Notifications

Base: `a302c172`. Candidate in `/root/xiuxian-harvest-notification-20261006`.
Main worker is still on `f7958deb`; no main restart planned for notification
changes alone.

## Evidence And Scope

Baji naturally harvested 5787 incense at October 6 08:21:06 CST. The module
sent an immediate ordinary notification, confirmed receipt
`b0c501ee06b84d538c621e558ff7c8fa` (89 UTF-16 units, three lines, no mention).
The next harvest is 16:21:06. This is one gameplay action, not a duplicate
harvest; the problem is its notification route under the user's summary policy.

`run_cave_public_small_world_sync` previously used `normal` for every action.
Only a confirmed collect with `ok is True`, a current complete snapshot, no
error and no Retry-After now uses the existing `low` summary route. Resource
shortage remains high; partial, unknown, failed, rate-limited and non-collect
actions retain the original priority. Existing no-op low behavior is unchanged.

No formatter, aggregation schema, sender or retry mechanism is added. No
changes to player ownership, snapshot application, resource accounting, the
8-hour harvest timer, prayer schedule or automatic refining switches. Preserve
all existing queued/held records. A low enqueue acknowledgment is not a
Telegram delivery acknowledgment.

## Verification

Before the patch: three new tests failed at the original immediate route,
ten boundary tests passed. After the patch: focused lifecycle/runtime,
notification acceptance and formatting tests **301 passed / five subtests**.
Frozen full suite: **16058 passed / 1461 subtests**, 457.07s;
JUnit `/tmp/xiuxian-harvest-notification-full-20261006.xml`.
Second maintainer review: **422 passed / 43 subtests**, covering small-world
business/lifecycle, public entry, summary persistence and log-group display.
Two initial review invocations named a nonexistent test file and collected
zero tests; the successful run used the actual log-group display suite. No
test failure was suppressed. Ruff, compilation and `git diff --check` pass.
This is another pass by the same maintainer, not an independent external audit.

Both full-cycle and harvest-only modes keep one action and one notification
call, and the next scheduler visit skips until its existing timer. The real
runtime summary/store/flush chain runs with mock transport: no immediate send,
one persisted row, one send after the normal window, material text retained,
and no admin mention. Other tests cover unconfirmed action, failed flow,
incomplete snapshot, diagnostic error, Retry-After, resource shortage and all
four other spending actions. Existing cancellation/identity/receipt tests are
retained. All tests use isolated state with `XIUXIAN_ALLOW_LIVE_TEST_DB=0`.

Natural absence of immediate harvest notification can only be accepted after
normal maintenance loads the patch. Do not force another harvest for testing.

## Live Checkpoint

WA naturally harvested 8787 incense at 08:35:21, stock 180908, next harvest
16:35:21. The old runtime sent confirmed receipt
`5e4c9fbb269b4f0d9feb306990cb4365` (92 UTF-16 units, three lines, no mention).
This supports the old-route baseline, not acceptance of the staged reduction.
Both automatic refining switches remain off.

WA and Baji YuanYing launches succeeded at 08:40:56 and 08:43:28 after their
due times; earlier channel launches also have future timers and no pending
probe. All 24 deep-retreat snapshots have identity verification, active state
and future server deadlines. The 19 channel send-as freezes do not disable
their eligible public-entry HTTP path. No settings or gameplay were altered
for these checks. Main, observer and watchdog remain active with NRestarts=0;
the only health warning is the unchanged historical held notification.
