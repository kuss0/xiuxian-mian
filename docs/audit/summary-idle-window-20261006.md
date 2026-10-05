# Summary Idle Window

Base: `8f761183`. Lab: `/root/xiuxian-summary-idle-window-20261006`.
Status: `84fbcffc` deployed with `99a43a45` on 2026-10-06 and pushed.

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

## Rollout

Main started at 00:44:52 and bootstrap finished 00:45:14; supervisor/worker
2076326/2076338, zero automatic restarts. Observer/watchdog were not restarted.
Post-merge isolated tests: 192 passed. At 00:45, all 24 identity rows and 59
enable columns across identity/module/runtime tables match the backup, as does
the complete MiniApp configuration. The unknown held batch is byte-for-byte
equivalent after decoding, with no replay or deletion. Ordinary pending rows
remain scheduled inside the restart's regular 30-minute window (01:14:53).

Backups: `/root/xiuxian-before-summary-fishing-20261006.db` and
`/root/xiuxian-summary-before-summary-fishing-20261006.db`, both mode 0600,
quick_check ok. No DB restoration. Production quiz learning remains dirty.
At 00:46 watchdog is ok; health warns on old held plus the recent rejected
meditation result. The earlier 429 is no longer in the critical window,
not proof that its endpoint restriction was removed.

Stop at 00:43:32 was controlled but not fully drained: a 15.622s fate interpret
request completed at 00:43:40. Background cleanup exceeded its wait twice,
so final whole-state save was correctly skipped. The per-action interpret
receipt was persisted with no pending operation. Do not describe this as a
fully clean shutdown; its subsequent natural resume remains under observation.
See the matching fishing rollout report and backlog follow-up.

## Natural Follow-Up

At 01:14:54 the first post-release ordinary digest was confirmed by the Bot:
receipt `2b74c35f23644ed381505475d88900fd`, hash
`ae4dd924d69f68caa7e1d63b520dc1e4d56b060d20c234fd4427de849eb63906`,
1786 UTF-16 units, 23 lines, no explicit mentions. At least 58 confirmed wild
results had been observed in its pending batch, including WA's protected
exploration. This establishes natural summary delivery, not a new synthetic
test message or full notification-policy acceptance.

After the confirmed digest emptied the bucket, the first new result at
01:15:15 persisted a fresh deadline of 01:45:15, rather than the previous
batch's 01:44:53. The original unknown batch is still held, count unchanged.
The extreme long-idle case is covered offline; it has not naturally recurred.

WA's 01:01:03 prediction (1282187) and 01:01:17 change (1282191) were confirmed
at 01:01:04 / 01:01:20. Protected deep wild calls at 01:04:13 and 01:14:09
each returned cultivation +45000, with the saved server quota 2/8, remaining
6. Tianji reached 132. Fresh predictions after consumption (1282261 and
1282372) were observed; there was no manual forced exploration for acceptance.
The serial queue, not missing protection, delayed the second action.

Endpoint rate limiting is still open: three external-fate rejections reported
Retry-After 115, 141 and 1033 seconds. The latest shared deadline was 01:15:04
and was respected. Daily capture peak remained 32/min; no global-90/min breach
was established. The stopped interpret's owner later encountered this entry
limit before reaching its quest state, so continuation acceptance is pending.
Do not discard its saved interpret or retry the old action blindly.
