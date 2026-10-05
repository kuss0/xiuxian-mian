# Native Fish Gift Queue

Base: `8cb28eb4`. Lab: `/root/xiuxian-native-fish-gift-queue-20261005`.
Status: candidate accepted; deployment checkpoint pending.
Initial audit and second review are separate maintainer passes, not independent
external reviews.

## Scope And Evidence

The old `lab/fishing-timing-canary-20261001` queue candidate was held because
its storage batch handoff was volatile. Production `2ef8208c` now supplies the
durable sender boundary; this batch ports only the native settlement-to-queue
logic onto it, not the old sender or whole Lab.

Before the patch, tests reproduced a confirmed native catch updating inventory
and the accounted marker without extending the configured gift queue. Eight
tests failed against the base. There is no configured production gift target,
so this is a reachable missing automation step, not an observed lost transfer.

- Sixteen production lines append only confirmed catches, not bonus loot or
  bait. Inventory, pending queue, deadline and accounted marker share the
  existing checked SQLite commit and rollback.
- Preserve existing queued quantities and nonzero deadlines. Malformed JSON,
  non-mappings, invalid names/counts, Boolean/negative counts and overflow
  cannot silently clear the queue. The existing gift handoff performs final
  item/owner/capacity/command validation before sending.
- Queue only with standalone fishing enabled, a nonzero configured target,
  unchanged captured plan and normal scheduling authority. Public-only fishing
  permission does not authorize Telegram gifts. Cancellation, canary accounting
  and mid-flight plan changes can still account confirmed inventory but do not
  create new gift work.
- Reuse the existing gift ledger, storage task, send guards and receipt parser.
  No new command transport, schema, endpoint, retries, UI or account switches.

## Tests

Focused: **300 passed, 21 subtests**. Final frozen full-suite acceptance:
**15895 passed, 1436 subtests**, 451.96s; JUnit
`/tmp/xiuxian-native-fish-gift-queue-accepted-20261005.xml`.
Second review re-read captured-plan authority, queue/receipt commit order,
rollback, cancellation, corrupt counts, bonus exclusion and restart transitions.
No remaining blocker found for this producer-only change. Second-pass tests:
**401 passed, 21 subtests**, including the real storage transfer and fishing
report paths. Ruff, compilation and diff checks pass; frozen hashes remain
unchanged. All runs use `XIUXIAN_ALLOW_LIVE_TEST_DB=0`.

Coverage includes actual isolated SQLite reloads, queue/inventory atomicity,
exactly-once projection, save failure, existing deadlines, malformed queues,
bonus exclusion, disabled/public-only/canary authorization, changed target or
queue, and worker cancellation. The end-to-end test uses the real durable gift
handoff and storage receipt parser: wait behind another task, restart queued,
send once, confirm once. A separate branch restarts after an unknown send and
verifies it is held without a second command. Only game transport is mocked.

Frozen SHA-256:

```text
344d5d74d1e9bf89f8746be8a67dc54702d8bd974675b3369eda37e5762ac873  model/features/fishing_dwelling_store.py
c038a495e1b5b645ca173f619f665a499050e8b2e1ca235560a24a1eee22733c  tests/test_fishing_dwelling_store.py
c2afd59a025ab68941642dd83ecbd2c5216e2b1f4b9da4c7cf9ed53198b2104c  tests/test_native_fish_gift_queue.py
```

## Deployment And Remaining Debt

Only promote after final acceptance and second review, with no active game
pending. Preserve all targets, enable flags, the recently deployed Boss timing
patch and held notification batch. Do not enable a target for live sampling.
No schema migration; rollback this queue producer only, retaining the deployed
durable handoff consumer and all saved obligations. Never restore an old DB.

Native fish-open remains unimplemented: the confirmed native controller's
`open()` opens its panel, not a caught fish. Eight identities retain the old
auto-open setting, but it is not evidence that native open-fish actions run.
No blind endpoint calls or hidden resource consumption are introduced here.
Public-only gift transport and general manual storage batch durability/retry
semantics remain separate work. A real gift receipt still needs an authorized
configured target; isolated testing is not production delivery evidence.
