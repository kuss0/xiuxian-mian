# Active Query Cleanup Ownership

October 10, 2026 CST. Base `e5f73ff6`. Candidate:
`/root/xiuxian-phaseful-query-cleanup-20261010`.

## Scope And Reproduction

`_send_active_summary_query` clears an old summary trigger before sending a
status query. A success reply, settlement, disable, reschedule, changed probe or
anchor, identity removal or replacement during that await makes the old query
obsolete. The old continuation nevertheless sends and installs another wait.

Offline baseline: 34 failed, 4 passed, covering deep retreat and YuanYing with
and without an existing probe reservation. Two cases use the real deep-retreat
success reducer. These are injected interleavings, not 34 live incidents.

The fix adds three lines: capture the existing seven-field identity-object
guard after the query's initial reservation reset, then return false when
cleanup returns to a superseded operation. Ordinary message bookkeeping and
unrelated profile changes still allow one query using its actual receipt time.

No new helper, state field, cooldown, retry, sender, listener or MiniApp behavior
is introduced. Query callers return from the scheduler tick; cancellation does
not immediately fall through to a new command in that tick.

## Verification

Every pytest command uses `XIUXIAN_ALLOW_LIVE_TEST_DB=0`.

- Focused cleanup/early-result/summary/routing: 223 passed.
- Full frozen regression: 17,180 passed, 1,517 subtests, 456.25 seconds;
  JUnit `/tmp/xiuxian-phaseful-query-cleanup-full-20261010.xml`.
- Second source review checked reservation order, all direct call sites and
  scheduler returns. Cross-module check: 398 passed, 2 subtests (Tianxing retreat,
  native YuanYing, sent observers, startup guards and phaseful routing).
- Additional native retreat, identity binding, transport timeout and module
  timeout regression: 235 passed, 312 subtests.
- Ruff, py_compile and diff whitespace checks pass.

This is the same maintainer's second review, not an independent external audit.

## Release Boundary

Validated for merge, but do not restart production just for this preventive
guard. Worker `460132`, started 10:51:13, continues the already-loaded fragment
fix `5745a5d2`. Load this guard at the next controlled maintenance window and
separately observe natural behavior. No live state correction belongs to this
patch. The earlier WA correction and 22m59s maintenance gap are recorded in
[the fragment incident](concubine-fragment-projection-20261010.md).

Remaining debt: changes during the actual query/active/passive send await;
legitimate `summary_due -> observing_summary` transitions must remain valid.
The cleanup helper's own bookkeeping ownership, other notification waits,
fallback/relaunch and finalization await boundaries remain separate. This fix
does not claim those branches are protected.

## Live Checkpoint

At 11:29, primary/watchdog/observer services remained active, watchdog okay,
no pending commands or stuck runtime phase. Health score 95 reflects five held
notification batches; they are not automatically replayed. Foreground monitor
`13865` continues, with expiry around 17:30.

- WA is sailing after the 10:53:53 MiniApp launch; return 16:53:55, maintenance
  17:01:22. Baji return 12:06:49, maintenance 12:14:38.
- 11:21:18 ordinary summary confirmed, receipt
  `e9b6db4769a84457a3274982727e4980`, 934 UTF-16 units, 23 lines, no mentions.
- Today's fate cards are 24/24 settled, final at 03:03:41; three-line daily
  notification confirmed at 03:03:49. Yesterday's 23/24 must not be reused.
- Read-only native fishing report shows 20 recorded catches today. Captured
  MiniApp traffic peaks at 50 requests/60 seconds against the 90 limit.
- All 19 channel identities have today's native deep-retreat records. WA's
  next deep-retreat action on October 13 is backed by the actual start snapshot
  (359999 seconds remaining), not inferred as a missed 8-hour cycle.
- xuruode1's channel voyage timer is still from August 14; group sending remains
  frozen and its available MiniApp records do not include a new voyage status.
  Keep the preflight warning. Do not claim its voyage is healthy or issue a
  return/relaunch based on the old local partner snapshot.
