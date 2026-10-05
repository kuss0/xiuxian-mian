# Yinluo Daily Supply Priority

Base: `e9e012b7`. Status: `f57e2b05` deployed and naturally verified.

## Evidence

At 2026-10-06 02:59 CST, identity 8613500668 has Yinluo and daily
sacrifice enabled, last_daily_sacrifice_day=2026-09-18 and an expired
next_daily_sacrifice_time. The native banner observed on October 5 has
sha=440, exhausted slot 1, no pending operation or financial hold.
Cultivation accounting has no baseline; the displayed 11 is not spendable
evidence. The scheduler repeatedly selects soothe, records the missing
cultivation baseline, and moves auto_next_time one hour ahead. Due sacrifice
allows re-entry during that wait but loses to soothe again.

## Change

Move the existing due-sacrifice selection immediately before soothe, keeping
the banner-hint prerequisite. Remove its now-unreachable lower selection.
Unknown balances still cannot fund soothe/conversion. No new send, fallback,
state schema, configuration, timer, accounting projection or notification API.
The existing action executor continues to own dispatch and receipt handling.

Unchanged higher-priority gates: module/identity eligibility, accounting holds,
pending operations, sha calibration and explicit recovery, stale/missing banner,
pending settlements, ready collection and due-slot calibration. Daily toggle,
day and cooldown checks, phaseful settlement guard and capacity remain intact.

The patch fixes daily-supply starvation only. It does not make cultivation
available or enable any other resource-consuming action. Baseline acquisition
is a separate follow-up requiring native evidence, not a copied UI value.

## Validation

- Before the patch, the new regression fails for missing, insufficient and
  sufficient cultivation: first two send nothing; the last sends soothe.
- Focused scheduler, accounting runtime, send lifecycle and UI: 250 passed,
  11 subtests. Missing-banner, stale/calibration, ready collection, legacy
  pending, phaseful guard, daily toggle/day/cooldown and pending no-repeat
  are covered. Sending daily does not debit or invent cultivation.
- Test fixtures clear preceding subcase observations before seeding native
  resources; an initial fixture-isolation failure was corrected, not hidden
  by changing the runtime. The early full run was interrupted and is not
  acceptance evidence.
- Frozen full suite: 15924 passed, 1449 subtests, 457.15s;
  `/tmp/xiuxian-yinluo-daily-priority-20261006.xml`.
- Separate maintainer review: 1428 passed, 41 subtests, 178.48s. Re-read the
  real dispatch guard, financial admission, pending receipt retention, daily
  result reducer and priority branches. No independent external audit implied.
- Ruff, compileall and diff checks pass. Runtime change is four lines added
  and four removed, with no shared transport or persistence changes.

## Release And Observation

Only promote after full validation and a separate maintainer review pass.
Use the main repository fast-forward, preserve runtime quiz learning, and
back up stopped-state SQLite databases. Code rollback must not roll back game
state. Do not change identity controls, mutate production timers, force an
action, or replay held notifications for acceptance.

Watch the normal scheduler for one daily sacrifice, a strictly owned official
reply, updated daily/CD state and no repeated send. Continue natural fishing
acceptance around 03:18 (jfdffdddd) and 04:18 (WA). The fate 5-to-0 discrepancy,
historical unknown notification delivery and shutdown-drain work stay open.

## Production Acceptance

Normal stop completed at 03:12:06 with no incomplete-cleanup warning. Backups
(0600, quick_check=ok):

- `/root/xiuxian-stopped-before-yinluo-daily-20261006.db`
- `/root/xiuxian-summary-stopped-before-yinluo-daily-20261006.db`

Fast-forwarded into main; post-merge isolated tests: 137 passed, 11 subtests.
Before restart, identity, module, timer, runtime and meta tables matched the
stopped snapshot. Main start 03:14:14, bootstrap 03:14:33; pushed to
`xiuxian-mian/main`. Watchdog and observer stayed running. Their 03:12/03:13
inactive observations were this planned stop, not automatic restart failures.

At 03:14:36 the ordinary scheduler sent `.每日献祭` once, message 1282822 in
chat -1002083016447. Official bot 7965897083 replied directly at 03:14:38,
message 1282823: sha +500. Operation is complete, sha 440 -> 940, daily day
2026-10-06, next daily time 2026-10-07 00:01. Cultivation accounting remains
unmodified and uncalibrated. At 03:16:38 soothe was safely blocked again and
waits until 04:16:38; the now-completed daily action no longer bypasses that
wait. No repeat sacrifice or resource-consuming fallback was sent.

All module controls and full MiniApp configuration still match the snapshot.
The historical held notification is unchanged. Health has only that existing
delivery-review warning; watchdog is ok. Quiz learning remains unstaged.
