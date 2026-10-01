# Evening Native Fishing Canary

The user corrected the earlier assumption that today's fishing was complete.
At 22:54 the server reported only 1/5 used, four remaining. The sole earlier
cast at 00:12:51 ended missed; no regular daily native batch had completed.
The user's renewed request authorized continued testing, not a bulk rollout.

## Deployment And One Attempt

Only timing candidate 789073a7 was cherry-picked onto production f69f694e as
a22b4433. Exact candidate regression: 2243 tests and 196 subtests passed.
An online consistent SQLite backup passed quick_check before deployment:
`/root/xiuxian-native-fishing-rollout-20260930/pre-timing-20261001-2306.db`.
The agent explicitly restarted the main service at 23:08:10; observer was ok
at 23:08:34. No flags, identity selections, gift queues or voyage logic changed.

A normal admin `.login` equivalent (`.登录`) obtained a standard UI session.
One authenticated `fishing_native_canary` action ran for identity 7538826434.
It captured context at 23:09:49 and cast at 23:09:50 (332ms HTTP elapsed), but
returned `native_hook_outside_window` at 23:10:20. No hook, checkpoint, fight
or additional cast was sent. The original owned-session receipt was preserved.

A subsequent scoped read at 23:29:56 confirms the same session missed by
timeout: used 2/5, remaining three, caught=false, no bonus loot. The local
receipt still awaits normal scheduler recovery, due at Oct2 00:00:02. This
read-only probe did not account it, change timers or start another cast.
Evidence files (private, not committed):

- `/root/xiuxian-native-fishing-single-canary-20261001-2308.json`
- `/root/xiuxian-native-fishing-after-second-20261001.json`
- Today's production fishing capture JSONL.

## Additional Local Wait Defect

The flow computed a relative bite delay, then ran receipt/owner checks and
slept the original full delay. Validation latency therefore extended an
absolute game deadline. Reproductions with six and ten seconds of pre-sleep
validation failed before the fix and complete the offline flow after it.

The narrow correction starts the wait clock before validation and subtracts
elapsed validation time from sleep. All before/after checks remain. A 30-second
check still refuses the expired hook and retains its receipt. Exact on-host
validation latency was not instrumented for the failed live attempt, so this
is a reproduced contributor, not proof that all remaining latency is solved.

Expanded isolated regression: 2246 tests, 196 subtests passed in 37.57 seconds.
Full real catch acceptance remains open. No claim of live caught fish is made.
The other native Lab candidates remain undeployed; normal public scheduling
still uses the external path except for native receipt recovery.
