# Second Soul Progress Notices

Base `310477d5`, isolated worktree
`/root/xiuxian-second-soul-notification-progress-20261009`.
Not loaded or naturally accepted. Production remains unchanged.

## Evidence And Scope

On October 9 at 08:26-08:29 CST, WA's routine summary queue contained a status
wait, return with rewards, purge sent, purge confirmation and training
confirmation. The game completed normally: cultivation +77840, soul experience
+3544 on return; demon contamination 62, then 10 after one purge; training
resumed. The five separate routine records are unnecessary notification noise.
The old-group return is 12671920; new-group purge 1307181 -> 1307182 and
training 1307183 -> 1307184 each occur once. The purge costs 5000 cultivation;
the return reward above is gross, not net profit. They are not five game sends.
The 08:48:20 summary delivered this old behavior
in 14 lines without mentions, receipt `11eadde7e840441f9a3f085cb06feb50`.

Only `model/features/second_soul.py` notification call sites change:

- Ready/cultivating status panels become local console observations.
- A strictly known `sent` purge logs locally; unknown sending retains its
  existing notifications and recovery. A send receipt is not completion.
- Below-threshold purge/demon-status confirmations log locally. At/above the
  configured threshold retains existing notices, including the attempt cap.
- Return rewards, final training confirmation, injury, heart-demon decisions,
  timeouts and failures retain their existing notification paths.

There is no new summary schema, bucket, parser, receipt ledger or shared
sender change. No phase, cooldown, threshold, command, ownership, persistence
or retry condition changes. Old queued rows are not deleted or reclassified.
This is source-level removal of redundant process notices, not a claim of
transactional reward aggregation or a net-gain ledger.

## Validation

Before the change, five new assertions failed (including subtests), four
tests and two control subtests passed. The failures reproduce known-sent,
ready/cultivating and low-contamination notices still reaching TG admission.
The unknown-send and at-threshold controls retain their notices.

Focused second-soul/lifecycle, message/summary and notification acceptance
regression: 302 passed / 13 subtests. A real handler chain with mocked game
transport asserts one purge then one training command, the retained return
rewards and final confirmation, exactly two audit calls, and unchanged final
phase/contamination. Duplicate final replies do not notify again.
Frozen full regression: 17010 passed / 1517 subtests, 456.10 seconds. JUnit:
`/tmp/xiuxian-second-soul-notification-progress-full-20261009.xml`.
Second maintainer review reread the diff and the retained unknown, high-moran,
injury and heart-demon branches; independent test selection over observer,
summary persistence, lifecycle, scheduling, watchdog and messages passed
516 tests / 46 subtests. This is not an external independent audit.
Ruff, compilation and whitespace checks pass. Every pytest uses
`XIUXIAN_ALLOW_LIVE_TEST_DB=0`. Loading and natural acceptance remain pending.

## Loading Boundary

Do not restart merely for notification formatting. After validation, merge
as a candidate for an existing maintenance window. Verify worker loading and
the next natural return/purge/train cycle before claiming runtime acceptance.
Retain historical unknown-delivery batches and the old child notifications
from the unrelated trial Lab. No game requests or live-state edits are needed
to validate this change.
