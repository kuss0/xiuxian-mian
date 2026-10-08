# Duel Daily Report Source Evidence

Base: `28bd47e5`. Candidate worktree:
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
