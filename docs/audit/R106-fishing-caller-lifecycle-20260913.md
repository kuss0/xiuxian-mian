# R106 Fishing Caller Lifecycle

Offline candidate: /root/xiuxian-main-rebuild-20260907.
Branch rebuild/stability-20260907; HEAD cbf152cd unchanged.
Same operating boundary as R105: no production/live/control/deployment writes,
no skill changes, no commits/pushes. R67 live cultivation remains unvalidated.

## Review And Contract Before Implementation

Public fishing and message-entry fishing use different exclusion locks. Their
awaited entry, worker and notification paths lack retained owner/plan checks.
The worker can now carry a confirmed cancelled result, but neither caller adopts
it. The reducer also drops partial catches on no-rod, and treats a partial HTTP
failure as an ordinary short-delay success. Daily-summary notification awaits
can mark replacement identities as already reported.

- Both entry paths retain the exact identity object/account and fishing plan.
  Share the existing per-identity game lock. Revalidate before later work,
  pass the guard to authorization/HTTP, and never revive a deleted identity or
  apply a result to its replacement/account rebind.
- Keep the public MiniApp switch independent from the legacy fishing switch.
  A message entry requires its existing fishing switch. Public scheduled work
  must keep the existing entry observation/configuration guard.
- Confirmed dispatched facts may finish for the same owner after a control
  change or cancellation; replacement schedules/phases/options are not theirs
  to overwrite. No-result cancellation cannot invent completion or retry CD.
- Adopt drained confirmed results before re-raising cancellation. Notification
  failure must not erase settlement or turn a completed callback into a new
  game attempt. Recheck ownership between successive notifications.
- Preserve partial catches/counts before no-rod or later HTTP failures, and
  honor returned Retry-After on partial failures. Do not count unready work.
- After a daily-report await, mark only retained, unchanged identity facts;
  do not write through the implicit current-identity fallback.
- No new persisted flag, ledger, automatic retry or transport controller.
  Forced-stop/unknown-effect recovery, historical replay idempotence and atomic
  inventory/result durability remain separate acceptance work.

## Acceptance

Real caller/reducer tests with mocked network and temporary state: admission
changes at each await, public/message mutual exclusion, same-owner confirmed
cancellation, removed/replaced/rebound owners, unchanged independent controls,
partial failures, notification failures and daily-report replacement races.

## Verification

- Initial caller/ownership reproducer: 50 failed, 3 passed;
  /tmp/xiuxian-r106-reproducer-20260913.xml. Daily-report follow-up: 10 failed,
  74 passed; /tmp/xiuxian-r106-report-reproducer-20260913.xml. Concurrent and
  failed-notification report subset: 14 failed, 2 passed;
  /tmp/xiuxian-r106-report-concurrency-reproducer-20260913.xml. Confirmed-count
  follow-up: 3 failed; /tmp/xiuxian-r106-count-reproducer-20260913.xml.
- Both callers now retain operation ownership, share the fishing lock, and
  pass guards through entry/worker awaits. They adopt same-owner confirmed
  results before propagating cancellation, preserving replacement scheduling.
  Notifications cannot discard settlement or write into replacement owners.
  Partial no-rod/HTTP failures preserve confirmed counts, catches, inventory
  and Retry-After. Finish acknowledgements alone do not count as settlements.
- Daily reporting retains owner/fact snapshots under one report lock; only
  unchanged, successfully notified snapshots receive the report marker.
- Final source suite: 95 passed, 1.24 seconds;
  /tmp/xiuxian-r106-source-final-20260913.xml. Includes real draining-worker
  cancellation and four temporary-SQLite reload cases, with backup/schema/
  snapshot globals explicitly isolated. Associated suite before the last four
  SQLite cases: 678 passed, 39 subtests, 29.56 seconds;
  /tmp/xiuxian-r106-focused-final-20260913.xml.
- Final network-isolated full suite: 11696 passed, 1275 subtests, 170.62 seconds;
  /tmp/xiuxian-r106-full-final-20260913.xml. JUnit contains 12971 cases and no
  failures, errors or skips. Configured Ruff, scoped compilation, pip check
  and git diff --check pass. All test sessions finished.

## Remaining Acceptance

At the R106 checkpoint the reducer still ignored false save_state results, and
its inventory helper saved independently. R107 subsequently repairs scoped
atomic result/inventory failure handling and local pending recovery. Historical
replay idempotence and unknown-effect/restart recovery are not certified by
the lifecycle tests. See R107-fishing-result-commit-20260913.md for its separate
evidence and rollout constraint. Direct-entry cross-day clocks, parser authority and
startup handling of unresolved MiniApp phases also remain open.

No production, service, live request, data/configuration, listener, skill,
commit or push changes. R67 production subordinate-role cultivation remains
unvalidated; this is not whole-project Final Review or rollout approval.
