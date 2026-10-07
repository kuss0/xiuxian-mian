# Semantic Bot Metadata Evidence

Status: read-only reporting repair, code acceptance complete.
No runtime reducer, listener, sender, switch, database or log-file mutation.

## Incident

Baji's October 7 15:37:19 panel in -1002083016447, message 1292849,
replied to script command 1292847. The live business reducer consumed it
normally and scheduled the next check at 21:40:41. Its log row lacked
sender_is_bot and sender_username, so the semantic report omitted it.

The same stable sender ID, 8735907987, was explicitly logged as bot
hantianzun32_bot at 14:46:16, message 1292455, replying to our command
1292454 in the same group. This supplies earlier anchored identity evidence
without guessing from the panel's content or changing gameplay.

## Boundary

Only the report's in-memory row view can gain a missing bot flag. A seed
requires literal sender_is_bot=True, a strict hantianzun<digits>_bot
username, a positive sender ID, valid chat/message keys and an earlier
script-owned small-world action root. A later seed cannot authorize an
earlier row. Account listeners and groups may differ; the numeric sender
identity is stable, but command/reply anchors still include chat ID.

Do not override explicit False, None or mistyped flags, conflicting names,
forwarded rows, invalid IDs or conflicting senders for the same message.
Any explicit non-bot evidence for an ID in the report window prevents its
use for inference. Explicit bot rows retain the preexisting report rules;
this patch is not a global redesign of direct metadata authority.
Later conflicting username metadata revokes that sender's inferred authority;
only another anchored official reply can establish the new name.

The helper makes linear passes over already loaded rows. It does not scan
Telegram, query the live DB, import runtime code or send notifications.
Scope exposes inferred_bot_rows; sources remain untouched. Missing chat
scope, manual commands and unrelated players still cannot become our panel.

## Validation

- Initial new tests: 3 behavior failures, 13 missing-diagnostic failures,
  11 passing negative controls. Second review reproduced two same-message
  sender conflicts and three stale-name cases; all now prevent inference.
  A positive control verifies re-establishing authority with a new anchor.
- Focused regression: 288 passed / 16 subtests; cross-module second review:
  244 passed. There are 33 new test cases.
- Ruff, compileall and git diff --check passed.
- Initial full regression passed 16944 / 1486 subtests. Final regression
  passed **16948 / 1486 subtests** after the last four cases. All pytest
  invocations set XIUXIAN_ALLOW_LIVE_TEST_DB=0; test sessions completed.
- Read-only actual-log replay: panels 5 -> 6; partially explained deltas
  1 -> 2; unexplained remains 2. The added Baji interval retains the
  14:32 disaster and the confirmed preach-to-100 reply but does not claim
  they explain the later faith value 98.

The voyage durability Lab is independent and is not included in this patch.
After full verification, merge only this tool, its test and this document.
No service restart is needed for this standalone report.

## Production Recheck

Merged as 4d961c51 on October 7 at about 16:03. Production-directory
isolated recheck: 82 passed. Read-only invocation now returns six panels,
two partially explained deltas and two unexplained deltas. No service
restart, game request, state correction or module switch change occurred.
The separate voyage record/schema prototype was not included.
