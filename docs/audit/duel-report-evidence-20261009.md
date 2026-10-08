# Duel Daily Report Source Evidence

Base: `28bd47e5`. Worktree:
`/root/xiuxian-duel-report-evidence-20261009`.

## Finding

`build_duel_daily_report` previously counted any matching battle text naming a
currently managed attacker, regardless of sender, chat, forwarding or event
type. A player's copied report could add another transfer. The reader also
assumed every JSON line was an object with coercible IDs, and the amount parser
could abort the whole report on a malformed/non-finite number.

These are source-review findings reproduced with isolated synthetic logs, not
a claim that tonight's delivered report was actually inflated. Red baseline:
22 failures, six passing tests and one passing subtest. Failures include the
missing sender checks, malformed row structure and malformed amount handling.

## Change

- Use the existing configured game-bot IDs and monitored game-group IDs,
  including both groups when enabled. Bot usernames or `sender_is_bot` alone
  never grant authority. Explicit forwarding/non-bot/invalid flag values are
  excluded; legacy absent optional flags still require a known sender ID.
- Require positive integer message IDs and nonzero integer chat IDs. Skip
  malformed JSON, non-object rows and invalid IDs rather than inventing an
  occurrence key or aborting other valid results.
- Preserve existing last-logged-row selection by `(chat_id, message_id)`.
  A non-report final edit removes the earlier report; distinct messages with
  equal rewards and equal message IDs in different groups remain distinct.
- Invalid amounts contribute nothing. A malformed winner amount does not
  manufacture a zero-gain target entry or discard a separately valid loss.

No game action, target selection, cooldown, batch counter, gameplay reducer,
notification priority, delivery retry, state schema or report schedule changes.
The existing 23:55 daily scheduler and report formatting remain in place.

## Validation

All pytest invocations use `XIUXIAN_ALLOW_LIVE_TEST_DB=0` and isolated state.

- Final focused duel/report/summary suite: 244 passed, 33 subtests.
- Second maintainer review with routing, resource observations, daily closure,
  rare reports and delivery tests: 158 passed, 39 subtests.
- Frozen full suite: 16996 passed, 1511 subtests, 451.28 seconds.
  JUnit: `/tmp/xiuxian-duel-report-evidence-full-20261009.xml`.
- Ruff, py_compile and git diff checks pass.

Read-only live compatibility check: all five Lpprceqei battle messages today
have the correct group, `sender_is_bot=true`, `forwarded=false` and sender IDs
already present in the configured bot set. Message IDs: 1305375, 1305451,
1305507, 1305533, 1305578. The later quota reply 1305645 is not a battle and is
not a sixth transfer. This metadata check does not send a test report or prove
the new code was loaded. No live database or game state was modified.

## Release And Limits

Merge/push only this narrow candidate after review. No additional manual
restart: the existing R2 backup timer is scheduled around 04:45:02 and normally
stops/restores the service. Verify in-flight work before that window and actual
service recovery/loaded code afterward. Until then worker 3816200 still uses
`f005d86d`; tonight's natural daily report is a later acceptance step.

This is not complete historical attribution. Current usernames do not recover
all historical rename ownership; current trust/group configuration can exclude
older sources; last-logged-row deduplication does not resolve conflicting or
out-of-order source history; official cross-group rebroadcasts do not carry a
shared battle key here. Durable delivery acknowledgement and unknown-outcome
retry behavior are unchanged. These remain separate debts, not silently solved
by adding a sender filter. This was a second maintainer review, not an independent
external audit. The unfinished trial/voyage Labs are not part of this release.

## Merged, Pending Scheduled Loading

`750d8561` was fast-forwarded into main and pushed to `xiuxian-mian/main`.
Production-directory tests used isolated state and passed 73 tests / 25
subtests. No service restart or test Telegram message was issued.

At 04:03 the agent tool server resumed after an interruption. Xiuxian itself
remained on worker 3816200/supervisor 3816199, started October 8 22:13:35;
observer 3435134 and watchdog 3435106 are unchanged. Main and remote tracking
both point to `750d8561`; the only production dirty file is the unrelated
learned quiz bank. The full-suite XML remains complete with zero failures,
errors or skips (18507 JUnit cases including subtests). Completed work was not
repeated.

Tool sessions 10441/93487 cannot be resumed after that server restart, although
their bounded journal readers remain as OS processes. New foreground session
18303 watches main, observer, watchdog and backup logs from 04:00 onward and
expires around 10:03. The old readers were not stopped. This is a resumed
observation boundary, not a claim of uninterrupted main-agent attendance.

At 04:05 the actual backup timer still targets 04:45:02. Both this report fix
and the one-line command-voyage grouping change remain pending runtime loading.
The two held historical notifications remain unknown and are not replayed.
Lpprceqei's 03:54:50 fate read remains 0 versus old accepted 1; next retry is
not before 04:24:50. Daily duel closure remains intact. Continue through backup
recovery and the 07:09:55 preparation / 07:19:55 rift windows.

## Scheduled Loading, October 9 04:49

The existing R2 timer started at 04:45:05. It stopped the active observer,
watchdog and main service; the main supervisor requested no-new-sends before
stopping its worker. This is not an application drain acknowledgement or a
fix for the separately recorded backup-admission debt.

Main started at 04:46:28 on disk revision `b686012b`, supervisor 3981690 and
worker 3981692. UI and 24-identity startup completed by 04:46:51. Watchdog
3981709 and observer 3981735 recovered at 04:46:31/04:46:34; the unconfigured
independent listener remained inactive. No manual restart was issued. This
loads `750d8561` and command-voyage grouping `638955a6`; the unfinished trial
and voyage Labs remain absent from production. Natural report/voyage delivery
is still a separate acceptance step.

Read-only comparison against the 24-identity pre-maintenance snapshot found
no changed identity enablement or module switches. The sole module-state
change was xuruode1's `last_tower_day`, matching its actual 04:40:29 completion
before backup. WA/Baji duel and refinement remain off; harvest and voyage on.
Lpprceqei retains today's duel closure and October 10 01:38:41 next run; WA's
rift remains October 9 07:19:55. Pending tasks/actions remain empty.

The two held notification batches have the same before/after SHA-256:
`060561ca1982bdb9b9a9c34bc2af5a85f37af3c010dac08b2e5a74e274cfc036`.
The tower reward notice survives and startup adds one ordinary notice; the
existing summary window is now 05:16:29. No old delivery is replayed.

Lpprceqei's natural 04:25:06 and post-startup 04:46:59 fate reads still report
0 against the old accepted 1. No settle/draw/choose was released. Restart
rebuilt an in-memory wait and caused the latter read before the pre-restart
earliest retry of 04:55:06. This is existing non-durable business-wait behavior, not acceptance
of persistence across restarts; the next wait now starts at 04:46:59.

Backup snapshot `7c9f0324` saved at 04:48:54; its check found no errors and the
service completed at 04:49:02 with exit 0. Restic emitted a missing HOME/cache
warning but completed; no external backup code or environment was changed.
Post-startup watchdog is healthy. Observer retains the two-held-batch warning;
the fate discrepancy remains tracked separately by foreground session 18303.

## Natural Acceptance Through 05:31

Baji sent one `.远航归来` at 05:13:57, message 1306473. Official bot
8980154525 replied with 1306474 at 05:13:59: cultivation +372, stones +147,
soul-nourishing wood 1, stored energy +6 and affinity +6. The confirmed
command-voyage reward entered the ordinary durable summary, accepting the
newly loaded `638955a6` path rather than only the pre-existing MiniApp path.

Summary receipt `fbdc05bcacff416288e0c5770fa2742a` confirmed at 05:16:29:
1373 UTF-16 units, 22 lines, no mentions, 645ms. Pending rows drained, held
batches stayed at two without replay. The next ordinary window is 05:46:29.
Trial wave 2 completed 12/12 at 05:14:01; its batch receipt
`0d4b9a65798f4bf0893b48320316972e` confirmed at 05:14:02, 119 units/3 lines,
no mentions. Child notices are still retained; none of this accepts the
unmerged trial handoff prototype or proves retry/cancellation reward coverage.

Baji's native fishing handoff completed without an early relaunch: supply
actions at 05:14:12 and 05:24:53 were separately accounted, not counted as
casts. Five casts settled at 05:16:17, 05:18:36, 05:21:09, 05:23:25 and
05:26:49. Saved daily catch: `青鳞小鲫` x3, `赤尾火鲤` x1 and `银须灵鲢` x1.
Both native journals are accounted;
quota is 5/5 and next fishing is October 10 00:00:04. A native moon-voyage
launch followed at 05:26:54; state is sailing, return October 9 11:26:58 and
next concubine maintenance 11:31:38. No manual game action or state edit was
used to finish this handoff. The larger durable voyage Lab is still unmerged.

The 05:29 read-only fishing report totals 18 casts across four identities;
13 no-companion and six no-rod skips account for the other selected identities,
with no journal warnings. This is retained current-day evidence, not a full
historical fishing ledger. WA's Mulan collect/publish/support chain also
completed at 05:10:53, cooldown phase and pending reply zero.

The sole new mention was a real rare Hehuan item notice at 05:20:55, receipt
`505f75fe267e43d5aa5d1533653b8031`, 82 units/3 lines. Through 05:29 there are
17 instrumented confirmed deliveries, zero repeated payloads, one mention.
Other uninstrumented senders are excluded. A log-bot callback connection reset
at 05:21:43 recovered at 05:21:55 without a patch or restart.

Lpprceqei's 05:19:11 fate read still reports progress 0 against old accepted 1;
next ordinary read is not before 05:49:11. It remains the only unfinished fate
record, not a new failed settlement. Main and monitors retain the post-backup
PIDs. Foreground 18303 remains active. Upcoming observations: 05:46 summary,
WA voyage maintenance around 06:32, then 07:09:55 preparation and 07:19:55
rift. Do not advance the business CD or treat deep retreat as a Tianxing block.
