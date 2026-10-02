# Fishing Retry Within The Voyage Handoff

## Reproduction

The native worker correctly distinguishes `fishing_companion_sailing` from
`fishing_companion_missing`: sailing is temporary, not a daily skip. However,
its fixed 30-minute retry was longer than the new 15-minute post-return window.

An isolated regression reproduces the lost opportunity: at T the native
context reports sailing; the companion returns at T+60s; fishing was scheduled
for T+1800s. Since that timer is beyond T+960s, voyage launch sees no usable
fishing window and can immediately sail again. The regression failed against
the deployed code. This is a reproduced scheduler defect, not evidence that
all historical missed fishing was caused by this path.

## Narrow Repair

Only `fishing_dwelling_runtime.py` changes in the runtime:

- Temporary sailing uses a 10-minute recheck floor, shorter than the existing
  15-minute handoff. This does not shorten an explicit server retry-after.
- When the same identity has a finite future return clock and sailing state,
  wait until that return plus 60 seconds if later, avoiding repeated reads
  during a long voyage. This clock schedules a read; it never authorizes cast
  or voyage settlement without fresh server evidence.
- Unknown or stale return clocks do not manufacture voyage completion.
  No forced return, timer reset, new sender, retry of a mutation, or new module
  flag is introduced. No-rod/no-companion/quota terminal policies are unchanged.
- Existing owner checks, persistence rollback, locks and bounded launch hold
  remain unchanged. A server cooldown longer than the handoff still takes
  precedence; fishing does not indefinitely block another voyage.

## Validation

The focused fishing/concubine/caller lifecycle suite passes 411 tests. New
coverage verifies the previously failing handoff, invalid/stale return clocks,
long-voyage read scheduling, longer server backoff, save failure, and no false
daily completion. Malformed clocks are tested independently of persistence;
the separate failed-save case verifies the original timer is retained.

Ruff, compileall and diff checks pass. Full isolated suite: 15,436 passed,
1,399 subtests passed in 431.37 seconds. This candidate excludes the gift bridge on
`lab/fishing-timing-canary-20261001`.

## Natural Acceptance Still Required

The 23 selected fishing identities retain their October 3 midnight timers.
Today's Baji/WA voyages have return clocks 13:33:03/14:11:28 and scheduler
checks 13:34:36/14:20:33. They do not gain an unscheduled fishing run today.
Verify future sailing-to-return-to-fishing transitions at natural eligibility,
and verify the next day's aggregate report without modifying historical rows.
