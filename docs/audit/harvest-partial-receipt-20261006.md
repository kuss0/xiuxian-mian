# Partial Harvest Receipt

Base: `5cf6ad88`. Lab: `/root/xiuxian-harvest-partial-receipt-20261006`.
This is a narrow follow-up to the routine harvest notification change.

## Natural Evidence

On October 6, 2026, the two scheduled MiniApp harvests each completed with
one successful collect request. Their authoritative action messages said:

| Identity | Time (CST) | Collected | Resulting Stock | Next Harvest (CST) |
| --- | --- | ---: | ---: | --- |
| jfdffdddd / 301299112 | 16:22:13 | 6031 | 236283 | October 7 00:22:13 |
| WalterWA2000 / 8659059191 | 16:35:39 | 8041 | 188949 | October 7 00:35:39 |

The action replies identify the account and confirm business completion,
but their partial snapshots do not contain smallWorld. The old runtime
correctly invalidates full-panel freshness, yet retains scalar stock
230252 / 180908 and still older MiniApp snapshot values 117137 / 108370.
Those stale numbers must not be presented as the latest verified inventory.
The original summary patch also requires a complete current panel, so these
genuine successes would still take the immediate notification route.

Capture: `data/state/miniapp_capture/cave_treasure-2026-10-06.jsonl`.
Confirmed old-runtime notification receipts:
`bcd29eb9faef479b8c04ae5f9eec4242` and
`00abc4350ac5434583413b16fd2b7140` (89 / 92 UTF-16 units, three lines each).
The investigating agent sent no game request or test notification.

## Change And Boundaries

- Accept only a successful, dispatched, explicitly completed collect with
  verified player ownership, a partial snapshot, no error/unknown outcome
  and no Retry-After. Strictly match the observed two-line receipt; optional
  paired bold is allowed. Reject invalid ranges, duplicate lines and
  conflicting parseable message/rawMessage balances.
- Under existing operation owner, panel and MiniApp-record freshness checks,
  apply only the authoritative stock. Keep panel freshness invalid and do
  not infer population, faith, stability or uncollected incense. The receipt
  is one bounded `last_harvest_receipt` entry in the existing MiniApp record,
  not a historical ledger or an additional database table.
- Proven partial harvests use the existing low-priority summary path.
  Unknown, unsuccessful, rate-limited and unrecognized replies retain the
  conservative existing route. Do not silence an error merely because it
  contains success-like words.
- No extra HTTP or Telegram action, no retry change, no switch change and
  no change to the eight-hour harvest clock or prayer schedule. The automatic
  refining switches remain off. A cancelled request may retain confirmed
  facts for its original owner, but cannot revive a disabled schedule.

Existing state-save acknowledgement behavior is unchanged. These tests do
not prove atomic or durable storage under a failed save. Likewise, a summary
enqueue is not Telegram delivery confirmation. Historical held notifications
are not replayed, dropped or marked delivered by this change.

## Review And Tests

First frozen full run: 16374 passed / 1461 subtests, 461.40 seconds;
`/tmp/xiuxian-harvest-partial-receipt-20261006.xml`.
Subsequent review adds cancellation, removed/replaced/rebound identity,
newer-record ownership, empty panel, disabled switch and notification failure
regressions. It also exercises the real summary queue/store/flush path with
the partial receipt and mock delivery. Cross-module result: 656 passed /
2 subtests. One initial added fixture incorrectly wrote into an empty getter
fallback; corrected it to use the existing state setter, without changing
runtime behavior. No failing test was suppressed.

Final frozen full run: 16382 passed / 1461 subtests, 447.30 seconds;
`/tmp/xiuxian-harvest-partial-receipt-final-20261006.xml`. Additional owner,
state-retention, display and public-runtime coverage: 473 passed / 119
subtests. Ruff, compileall and diff checks pass. All tests use
`XIUXIAN_ALLOW_LIVE_TEST_DB=0`. The second review is another maintainer pass,
not an independent external audit. Post-merge verification is separate.

## Loading And Live Acceptance

Production worker 2207227 still loads `f7958deb`; supervisor, observer and
watchdog have not restarted. No live balance is manually corrected. Code
acceptance does not mean the patch is loaded or that the next natural harvest
has been accepted. At 16:46, both identities are idle, harvest enabled,
refining disabled; their harvest timers match the table above. Health shows
only the two historical held notification batches, and watchdog is healthy.
WA and Baji naturally launched YuanYing at 16:41:17 / 16:43:47 respectively;
these observations validate the old runtime, not any staged runtime repair.

After a separately recorded maintenance load, observe the next scheduled
harvest: one collect, correct stock, full-panel freshness still false for a
partial reply, ordinary summary delivery, unchanged switches and no repeat.

Read-only notification checkpoint for 13:00-16:47: seven runtime transport
attempts, all confirmed, zero repeated identical payloads, one explicit
mention link, visible length P50/P95/P99 102/273/273 UTF-16 units. The
independent watchdog and report senders are outside this scope. Report:
`/tmp/xiuxian-notification-1647-20261006.json`. This is neither a complete
business deduplication proof nor acceptance of the not-yet-loaded fix.
