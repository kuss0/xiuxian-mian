# Passive Quiz Observation Health Classification

Base: `7540a33f`. Lab: `/root/xiuxian-quiz-observer-20261006`.
Status: candidate validated; observer-only rollout pending.

## Evidence And Scope

At 06:30:35 CST an unmanaged quiz timeout for `@SeanHandy997` was logged
locally. No corresponding Telegram delivery or summary row was found. The
observer nevertheless counted its `timeout` wording as a journal warning.
The existing console-only quiz behavior is correct; this is a monitoring
classification defect, not a failed game action or failed notification send.

Extend the existing passive-observation rule with the complete emitted quiz
header: target code tag, external timeout label, unmanaged/learning-only
marker and question prefix. Accept a bare console message or the actual
runtime timestamp, including the journald envelope. Words inside that external
question are not runtime failures. Incomplete markers, owned quiz output,
other modules and log-write errors retain the previous rules. Unknown formats
remain subject to the existing classifier.

No quiz reducer, learning persistence, notification sender, game scheduler,
retry, safety lock, identity setting or shared transport was changed. The
pre-existing benign filters are not broadened. This does not claim every
owned quiz issue was already observable under those older rules.

## Verification And Second Review

- Focused: 203 passed, 24 subtests.
- Full isolated suite: 16020 passed, 1461 subtests, 463.89 seconds.
- Separate maintainer review regression: 253 passed, 26 subtests.
- Ruff, byte compilation and diff whitespace validation pass.
- The actual 06:30:30-06:30:40 journal replay has one line, zero hard errors
  and zero warnings under the candidate; the old live observer recorded one
  warning. No new Telegram notification or game action was sent for testing.
- Tests cover bare/timestamp/journald forms, alert words inside questions,
  owned/unrelated failures, incomplete markers, a quoted marker inside an
  owned question, log-write errors, and mixed-line journal aggregation.
- All tests use `XIUXIAN_ALLOW_LIVE_TEST_DB=0`. Initial and second review are
  separate passes by the same maintainer, not an external independent audit.

## Deployment Boundary

Fast-forward this candidate only; preserve the learned production quiz bank.
Restart only `xiuxian-health-observer.service` after post-merge regression.
Do not restart the main worker, watchdog or inactive listener. No game or
notification database migration is needed. Rollback is code-only, followed
by an observer restart; never restore game state or replay held notifications.

The old 04:27 cancelled fishing operation and the October 5 held delivery
must remain visible under their existing policies. A warning disappearing
because its ten-minute journal window elapsed is not natural acceptance of
this fix; actual-log replay and observer loading are recorded separately.

## Concurrent Live Evidence

WA naturally sent `.推命 探索` at 06:39:48, command `1283802` in
`-1002083016447`. Official bot `hantianzun16_bot` (`7965897083`) replied
with `1283803` at 06:39:51. The timeline rebuilt from `blocked_replan` to
`downstream_released`. Existing change protection is backed by the saved
01:51 MiniApp result explicitly retaining it for 23 hours 57 minutes; no
additional change command was sent. The 06:49:44 rift execution still needs
its own natural reply and settlement check.
