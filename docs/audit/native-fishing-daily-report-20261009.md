# Native Fishing Report Coverage

Base: `638955a6`. This changes the manual read-only report tool, not gameplay.

## Evidence And Boundary

At 00:38 on October 9, the saved native fishing report contained 13 accounted
rods across three identities (5, 5, 3), while `miniapp_daily_report.py` printed
no MiniApp rewards. Its fishing reader only understood legacy capture records.
The ordinary runtime notifications and native fishing settlements were intact.

The CLI now uses the existing `native_fishing_report` read-only snapshot and
validators for fishing. Confirmed daily counters are included even if an
identity is disabled after completing them. Require an accounted native
receipt from the selected date; warnings, mismatched dates and unconfirmed
receipts are excluded with a coverage note. Never derive empty casts from the
number of fish or claim daily completion from the selected identity count.

One source is selected for fishing, so native daily totals are not added to
possibly overlapping legacy captures. Other supported capture-based games
remain unchanged. A custom `--capture-dir` stays offline/capture-only unless
`--fishing-db` is explicit. The pure `build_report` call also remains
capture-only unless given a database. A missing DB is not created, and missing
or malformed evidence is not presented as zero gameplay or zero rewards.

Historical support is limited to the selected date actually retained by the
current daily snapshot. Cross-midnight or newer native receipts cannot certify
an older snapshot here; those cases remain excluded, not reconstructed.
This is not a new reward ledger or a complete report of every MiniApp feature.

## Verification

- Red baseline: 13 new cases failed against the original CLI/report.
- Final focused report/delivery regression: 166 passed.
- Second maintainer pass: 421 passed, including native settlement authority,
  journal/supply, notification metrics and strict delivery behavior. This is
  not an independent external audit.
- A real temporary WAL database test runs the CLI in a separate process with
  network creation forbidden, checks the requested day, exactly one reward
  listing and unchanged SQL dump, and forbids importing live state/persistence.
- CLI delivery fixtures were updated for the added optional argument. The
  subprocess network guard initially replaced socket before SSL was imported;
  that test-only import-order problem was corrected before final validation.
- Ruff, compilation and diff checks pass. All pytest runs use
  `XIUXIAN_ALLOW_LIVE_TEST_DB=0`.
- Frozen full regression: 16990 passed /1486 subtests, 456.12 seconds;
  `/tmp/xiuxian-native-fishing-daily-report-full-20261009.xml`.

Read-only production evidence through the candidate CLI correctly reports
13 rods, with fish quantities 3/1/9. No `--send-log-group`, HTTP request,
runtime restart, live database mutation or configuration change was used.

## Live Continuation

Command-return notification repair `638955a6` is pushed but not loaded by
worker `3816200`; its separate full regression passed 16975 /1486 subtests.
The routine 00:30:31 batch was confirmed as
`c88433b555bd487dae909d77d7c65fe1`, 895 visible UTF-16 units /21 lines/no
mentions; the queue emptied. This is existing runtime behavior, not natural
acceptance of the unloaded command-priority change.

Both historical held batches still have only unknown transport receipts;
do not replay or erase them. WXJerry main/xuruodeaiban and Rust main were
SSH-fetched again and remain `aa9dba2`, `cd2a2e6`, `f18e89ac` respectively.

A fresh xuruode4 fate-entry HTTP429 at 00:38:56 returned Retry-After 73s.
The existing shared-entry floor is five minutes. Its next captured request
was at 00:44:56, HTTP200 /attempt1, 360 seconds later. The operation completed
its read at 00:45:23 and the observer cleared the transient alert. No retry
before the boundary, manual replay, restart or limit change was needed.
WA harvested 8415 incense at 00:44:05 (stock 247356) and naturally dispatched
its nascent soul at 00:44:30. Baji dispatched its nascent soul at 00:48:26;
next due is 08:48:29. No repair is claimed for these normal actions.

Foreground `10441` remains active. Next watches include the ordinary summary
at 01:02:22, WA wild prep 01:12:14 / due 01:22:14,
then rift prep 07:09:55 / due 07:19:55. Renew observation before 05:35.
The separate twelve-file voyage durability Lab is unchanged and unmerged.

This tool can load on its next CLI invocation after merge; it requires no
service restart. Production default-path read-only verification follows
merge. Runtime notification loading remains a separate maintenance step.

## Release

`1a94d30b` was fast-forwarded into production and pushed to
`xiuxian-mian/main`. Production-checkout isolated verification: 166 passed.
At 00:52 the default CLI, without database/capture overrides, returned the
expected 13 rods and fish counts 3/1/9. No notification was sent. Main,
observer and watchdog PIDs remained unchanged with NRestarts zero; only the
unrelated live quiz-bank file is dirty.

A separate gyurihero meditation attempt at 00:52:10 was explicitly rejected
with HTTP409 /`meditation_not_ready`. Its saved state is
`meditation_rejected`, no pending unknown action, retained fate trace +1 and
active cultivation quest 0/30. Captures preserve shapes, not the numeric
readiness values needed to distinguish stale overview from concurrent game
state change. Do not classify this as a lost reply or blindly retry. Subsequent
wild gains at 00:54/00:55 may satisfy the quest; verify through its next
ordinary fate read, expected no earlier than about 01:22:10. This watch is
unrelated to the report-only release and remains open.
