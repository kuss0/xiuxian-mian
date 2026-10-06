# Passive Quiz Observation Health Classification

Base: `7540a33f`. Lab: `/root/xiuxian-quiz-observer-20261006`.
Status: `7984b520` deployed and pushed; observer-only restart at 06:50:10.

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

## Production Checkpoint

Post-merge isolated regression: 203 passed, 24 subtests. Only the observer
restarted (new PID 2241531). Main supervisor 2207210, worker 2207227 and
watchdog 2178662 stayed running; NRestarts remains zero. The learned quiz
bank remains the only unrelated dirty production file.

06:50:10 and 06:52:11 snapshots retain the known cancelled fishing operation
and held delivery, without new journal errors. The historical quiz sample
was already outside the ten-minute window before deployment; the actual-log
replay above is its acceptance evidence, not those clean journal counts.

WA's natural rift command `1283915` was sent once at 06:49:45. Official bot
`hantianzun23_bot` (`8980154525`) returned `1283916`, initially at 06:49:47,
edited at 06:49:55 and finally at 06:49:58. Prediction hit, Tianji +1,
contribution +30, space fragment x1, fourth-grade demon core x5 and
nine-heavens thunder wood x1 were recorded. Pending and error cleared;
Tianji is 139, prediction consumption is recorded at the final edit time.
No game command was sent by this maintenance session.

The same reward has a confirmed urgent notification (`ca26034db9d04d60a92dc76e8a17c50e`)
and a separate ordinary summary row. That newly observed duplicate is a
separate notification cleanup item, not part of this observer patch.

## Natural Acceptance After Deployment

At 08:00:55 a new unmanaged timeout for `@huanxinshuimeng` appeared in the
main journal using the exact external/learning-only header. The bounded
08:00:40-08:02:40 journal contains that console line and no notification
receipt. Summary pending remains zero; the historical held batch and its
hash are unchanged.

The deployed observer's 08:02:53 snapshot includes three main journal lines
inside its ten-minute window and reports hard=0, warn=0. Its sole remaining
warning is the historical held delivery. Unlike the earlier replay, this
is a fresh event seen by the running observer after deployment. Natural
acceptance of the passive external-quiz classification is complete; it does
not close unrelated notification, quiz-parser or transport debt.
