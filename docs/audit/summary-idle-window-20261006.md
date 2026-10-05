# Summary Idle Window

Base: `8f761183`. Lab: `/root/xiuxian-summary-idle-window-20261006`.
Status: accepted candidate; production not changed yet.

## Evidence And Scope

The ordinary digest at 2026-10-05 23:14:05 CST was confirmed by the Bot.
Its empty checkpoint retained the next deadline, 23:44:04. The first new
record arrived at 2026-10-06 00:01:02 and the runtime armed its existing
30-minute delay. The checkpoint still advertised yesterday's deadline, so
the read-only observer reported pending records overdue immediately.
This is a stale deadline after idle, not evidence of a stopped sender.

The change records a fresh deadline when an empty bucket receives its first
new row. It preserves a later existing deadline and never renews a nonempty
backlog on subsequent arrivals. Locking, scheduling, the 1800-second floor,
held retention, receipt checks and transport remain unchanged. No game
configuration, schema, UI or action path changes.

The October 5 unknown batch remains held. Nothing in this patch retries,
deletes or marks it delivered. A real overdue nonempty bucket must still
raise the existing warning; the observer is not weakened.

## Verification

Before the production edit, both confirmed and unknown prior-delivery cases
failed with the old deadline (4600 instead of 11800). Focused candidate tests:
82 passed. Coverage also checks persistence, real overdue records, preserved
restart deferral, concurrent enqueue/flush, ambiguous sends, cancellation and
SQLite errors. Full-suite acceptance: **15899 passed, 1436 subtests**,
460.19s, `/tmp/xiuxian-summary-idle-window-20261006.xml`. Second review:
**200 passed, 47 subtests**, including actual notification routing and
background task shutdown. Ruff, compileall and diff checks pass.

Second review re-read the empty/nonempty boundary, startup deferral,
in-flight enqueue, unknown-held separation and save failure behavior. No
blocker found in this narrowly scoped change. Frozen runtime SHA-256:
`f79983cc7f97fa0ae0e2088b237ff495581bf4620cfa096dec5d8a7cd2194e87`.

Before deployment, the normal digest arrived at 00:31:02 with a confirmed
Bot receipt (`57780c0559d044fbad2ca41ef105e157`, 990 UTF-16 units,
23 lines, no mentions). This confirms the old sender was working and the
overdue warning was based on a stale idle deadline. It does not constitute
post-patch natural acceptance.

The initial audit and second review are separate maintainer passes, not
independent external reviews. Deployment requires no active game pending,
backups and unchanged identity settings. Rollback is code-only; never restore
an old game or summary DB or replay held notifications.

## Other Observations

- All three services active with zero automatic restarts; pending game queue
  empty. Independent listener is still intentionally unavailable.
- xueuode5 hook at 00:13 returned HTTP 409 `fishing_too_early` after a 388ms
  cast response and a 107ms hook response. The same rod was read back and
  accounted at 00:14 (empty rod plus bonus); following rods completed fights.
  Recovery correctness does not prove hook timing quality. Inspect asymmetric
  delay before changing timing; do not recast an unknown owned rod.
- WA and jfdffdddd sailing waits have saved fishing deadlines one minute after
  their known returns (04:18:23 and 03:18:11 respectively). Individual failure
  notifications for those expected waits remain separate classification debt.
- WA wild preparation is due around 01:00:58, execution at 01:10:58. No forced
  game request has been issued for these checks.
